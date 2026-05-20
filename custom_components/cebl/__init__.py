import logging
import aiohttp
import asyncio
import async_timeout
import re
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_interval
from .const import DOMAIN, API_URL_FIXTURES, API_URL_LIVE_BASE, API_HEADERS, PLATFORMS, STARTUP_MESSAGE

_LOGGER = logging.getLogger(__name__)

def _normalize_team_name(team_name):
    """Normalize team names so selections survive numeric ID changes."""
    return re.sub(r"[^a-z0-9]+", "", (team_name or "").lower())

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CEBL from a config entry."""
    _LOGGER.info(STARTUP_MESSAGE)
    coordinator = CEBLDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("CEBL integration setup complete.")

    # Schedule live score updates every 60 seconds for better real-time updates
    async_track_time_interval(hass, coordinator.async_update_live_scores, timedelta(seconds=60))

    return True

class CEBLDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching CEBL data from the API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        """Initialize."""
        self.entry = entry
        self.session = async_get_clientsession(hass)
        self.url_fixtures = API_URL_FIXTURES
        self.url_live_base = API_URL_LIVE_BASE
        self.headers = API_HEADERS
        self.teams = {str(team_id) for team_id in entry.data.get("teams", [])}
        self.team_names = entry.data.get("team_names", {})
        self.selected_team_names = {
            _normalize_team_name(name) for name in self.team_names.values() if name
        }
        if not self.selected_team_names and entry.title.startswith("CEBL - "):
            self.selected_team_names.add(_normalize_team_name(entry.title.removeprefix("CEBL - ")))
        self.match_ids = {}  # Store match IDs for live scores
        _LOGGER.info(f"Initializing CEBLDataUpdateCoordinator with teams: {self.teams}")
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=10),
        )

    async def _async_update_data(self):
        """Update data via library."""
        _LOGGER.info("Fetching CEBL data from API.")
        try:
            # Increase timeout to 30 seconds for initial data fetch
            async with async_timeout.timeout(30):
                async with self.session.get(self.url_fixtures, headers=self.headers) as response:
                    if response.status != 200:
                        _LOGGER.error(f"Invalid response from API: {response.status}")
                        # Return empty data instead of failing completely
                        if response.status in [503, 504, 502]:  # Service unavailable, gateway timeout
                            _LOGGER.warning("API temporarily unavailable, returning empty data")
                            return {"fixtures": []}
                        raise UpdateFailed(f"Invalid response from API: {response.status}")
                    
                    games = await response.json()
                    _LOGGER.debug(f"Fetched raw games data: {games}")
                    
                    # Handle case where API returns non-list data
                    if not isinstance(games, list):
                        _LOGGER.warning(f"API returned unexpected data format: {type(games)}")
                        return {"fixtures": []}
                    
                    # Filter games for selected teams and convert to expected format
                    fixtures = []
                    for game in games:
                        try:
                            # Handle both old and new API field formats
                            home_team_id = str(game.get("home_team_id", "") or game.get("hometeamId", ""))
                            away_team_id = str(game.get("away_team_id", "") or game.get("awayteamId", ""))
                            home_team_name = game.get("home_team_name", "") or game.get("homename", "")
                            away_team_name = game.get("away_team_name", "") or game.get("awayname", "")
                            home_team_match = (
                                home_team_id in self.teams
                                or _normalize_team_name(home_team_name) in self.selected_team_names
                            )
                            away_team_match = (
                                away_team_id in self.teams
                                or _normalize_team_name(away_team_name) in self.selected_team_names
                            )
                            
                            if home_team_match or away_team_match:
                                # Convert to expected fixture format - handle both API formats
                                fixture = {
                                    "id": game.get("id") or game.get("matchId"),
                                    "homeTeam": {
                                        "id": home_team_id,
                                        "name": home_team_name,
                                        "logo": game.get("home_team_logo_url", "") or game.get("homelogo", ""),
                                        "score": game.get("home_team_score", 0) or game.get("homescore", 0)
                                    },
                                    "awayTeam": {
                                        "id": away_team_id,
                                        "name": away_team_name,
                                        "logo": game.get("away_team_logo_url", "") or game.get("awaylogo", ""),
                                        "score": game.get("away_team_score", 0) or game.get("awayscore", 0)
                                    },
                                    "status": game.get("status", "") or game.get("matchStatus", ""),
                                    "competition": game.get("competition", "") or game.get("competitionId", ""),
                                    "venue_name": game.get("venue_name", ""),
                                    "period": game.get("period", 0),
                                    "start_time_utc": game.get("start_time_utc", "") or game.get("matchTimeUTC", ""),
                                    "stats_url": game.get("stats_url_en", ""),
                                    "cebl_stats_url": game.get("cebl_stats_url_en", ""),
                                    # Add live indicator from API - THIS IS THE KEY FIELD
                                    "live": int(game.get("live", 0)),  # 1 = live, 0 = not live
                                    "match_status": game.get("matchStatus", ""),
                                    "clock": game.get("clock", "00:00:00"),
                                    "period_type": game.get("periodType", "")
                                }
                                fixtures.append(fixture)
                                
                                # Extract match ID from stats URL for live scores
                                stats_url = game.get("stats_url_en", "")
                                if "/u/CEBL/" in stats_url:
                                    try:
                                        match_id = stats_url.split("/u/CEBL/")[1].split("/")[0]
                                        self.match_ids[game.get("id")] = match_id
                                        _LOGGER.debug(f"Extracted match ID {match_id} for game {game.get('id')}")
                                    except (IndexError, AttributeError):
                                        _LOGGER.warning(f"Could not extract match ID from {stats_url}")
                        except Exception as game_err:
                            _LOGGER.warning(f"Error processing game data: {game_err}")
                            continue
                    
                    _LOGGER.info(f"Filtered {len(fixtures)} fixtures for selected teams")
                    return {"fixtures": fixtures}
                    
        except aiohttp.ClientError as err:
            _LOGGER.error(f"HTTP error fetching games: {err}")
            # Return empty data for temporary network issues
            if "timeout" in str(err).lower() or "connection" in str(err).lower():
                _LOGGER.warning("Network connectivity issue, returning empty data")
                return {"fixtures": []}
            raise UpdateFailed(f"HTTP error fetching games: {err}")
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout error fetching games - API may be slow, returning empty data")
            # Return empty data instead of failing - sensors will handle gracefully
            return {"fixtures": []}
        except Exception as err:
            _LOGGER.error(f"Unexpected error fetching games: {err}")
            # Return empty data for unexpected errors to prevent complete failure
            return {"fixtures": []}

    async def async_update_live_scores(self, _):
        """Fetch live score data from the API using match IDs."""
        _LOGGER.info("Fetching live CEBL scores from API.")
        
        if not self.match_ids:
            _LOGGER.debug("No match IDs available for live score updates")
            return
            
        # Get current fixtures to check which games should have live data
        current_fixtures = self.data.get('fixtures', []) if self.data else []
        
        live_scores_data = {}
        
        for game_id, match_id in self.match_ids.items():
            # Find the corresponding fixture to check if game should be live
            fixture = None
            for f in current_fixtures:
                if str(f.get('id')) == str(game_id):
                    fixture = f
                    break
            
            if not fixture:
                _LOGGER.debug(f"No fixture found for game {game_id}, skipping live data fetch")
                continue
            
            # Only fetch live data if the game is actually live, about to start, or recently completed
            fixture_status = fixture.get('status', '').upper()
            start_time_utc = fixture.get('start_time_utc', '')
            
            should_fetch_live = False
            
            if fixture_status in ['IN', 'LIVE', 'HT', 'BT']:  # Game is definitely live
                should_fetch_live = True
            elif fixture_status in ['POST', 'COMPLETE'] and start_time_utc:
                # Fetch for completed games within the last 24 hours to get final stats
                try:
                    from datetime import datetime
                    import pytz
                    
                    if start_time_utc.endswith('Z'):
                        fixture_dt = datetime.fromisoformat(start_time_utc[:-1]).replace(tzinfo=pytz.UTC)
                    else:
                        fixture_dt = datetime.fromisoformat(start_time_utc).replace(tzinfo=pytz.UTC)
                    
                    now = datetime.now(pytz.UTC)
                    hours_since_game = (now - fixture_dt).total_seconds() / 3600
                    
                    # Fetch if game completed within last 24 hours
                    if 0 <= hours_since_game <= 24:
                        should_fetch_live = True
                        _LOGGER.debug(f"Game {game_id} completed recently ({hours_since_game:.1f} hours ago), fetching final stats")
                    else:
                        _LOGGER.debug(f"Game {game_id} completed too long ago ({hours_since_game:.1f} hours), skipping live fetch")
                        
                except Exception as e:
                    _LOGGER.debug(f"Error parsing start time for completed game {game_id}: {e}")
            elif fixture_status == 'SCHEDULED' and start_time_utc:
                # Only fetch for scheduled games if they're starting soon (within 15 minutes)
                try:
                    from datetime import datetime
                    import pytz
                    
                    if start_time_utc.endswith('Z'):
                        fixture_dt = datetime.fromisoformat(start_time_utc[:-1]).replace(tzinfo=pytz.UTC)
                    else:
                        fixture_dt = datetime.fromisoformat(start_time_utc).replace(tzinfo=pytz.UTC)
                    
                    now = datetime.now(pytz.UTC)
                    time_until_game = (fixture_dt - now).total_seconds()
                    
                    # Only fetch if game starts within 15 minutes
                    if -900 <= time_until_game <= 900:  # 15 minutes before/after start
                        should_fetch_live = True
                        _LOGGER.debug(f"Game {game_id} starts soon ({time_until_game/60:.1f} min), fetching live data")
                    else:
                        _LOGGER.debug(f"Game {game_id} not starting soon ({time_until_game/3600:.1f} hours), skipping live fetch")
                        
                except Exception as e:
                    _LOGGER.debug(f"Error parsing start time for game {game_id}: {e}")
            
            if not should_fetch_live:
                _LOGGER.debug(f"Skipping live data fetch for game {game_id} (status: {fixture_status})")
                continue
            try:
                # Use the new URL pattern: /data/[MATCH_ID]/data.json
                live_url = f"https://fibalivestats.dcd.shared.geniussports.com/data/{match_id}/data.json"
                # Increase timeout to 20 seconds for live data
                async with async_timeout.timeout(20):
                    # Use minimal headers for live scores API
                    live_headers = {
                        'Accept': 'application/json',
                        'User-Agent': self.headers['User-Agent']
                    }
                    async with self.session.get(live_url, headers=live_headers) as response:
                        if response.status != 200:
                            _LOGGER.debug(f"No live data for match {match_id}: {response.status}")
                            continue

                        # Force JSON parsing even if content-type is wrong
                        text_data = await response.text()
                        try:
                            import json
                            live_data = json.loads(text_data)
                        except json.JSONDecodeError as err:
                            _LOGGER.debug(f"JSON decode error for match {match_id}: {err}")
                            continue

                        if live_data:
                            # Extract comprehensive team information
                            tm1 = live_data.get('tm', {}).get('1', {})
                            tm2 = live_data.get('tm', {}).get('2', {})
                            
                            # Extract top scorers
                            top_scorers = live_data.get('sPoints', {})
                            top_scorer_list = []
                            for key, player in list(top_scorers.items())[:5]:
                                top_scorer_list.append({
                                    "name": player.get('name', ''),
                                    "points": player.get('tot', 0),
                                    "team": player.get('tno', 0),
                                    "jersey": player.get('shirtNumber', ''),
                                    "photo": player.get('photoS', '').strip()
                                })
                            
                            # Extract player details for both teams
                            team1_players = []
                            team2_players = []
                            
                            for team_num, player_list in [('1', team1_players), ('2', team2_players)]:
                                team = live_data.get('tm', {}).get(team_num, {})
                                players = team.get('pl', {})
                                
                                for player_id, player in players.items():
                                    if player.get('sMinutes', '0:00') != '0:00':  # Only players who played
                                        player_list.append({
                                            "id": player_id,
                                            "name": player.get('name', ''),
                                            "jersey": player.get('shirtNumber', ''),
                                            "position": player.get('playingPosition', ''),
                                            "minutes": player.get('sMinutes', '0:00'),
                                            "points": player.get('sPoints', 0),
                                            "rebounds": player.get('sReboundsTotal', 0),
                                            "assists": player.get('sAssists', 0),
                                            "plus_minus": player.get('sPlusMinusPoints', 0),
                                            "fg_percentage": player.get('sFieldGoalsPercentage', 0),
                                            "three_point_percentage": player.get('sThreePointersPercentage', 0),
                                            "photo": player.get('photoS', '').strip(),
                                            "starter": player.get('starter', 0),
                                            "captain": player.get('captain', 0)
                                        })
                            
                            # Extract officials
                            officials = live_data.get('officials', {})
                            official_list = []
                            for ref_key, ref in officials.items():
                                official_list.append(ref.get('name', ''))
                            
                            # Extract other games (league scoreboard)
                            other_games = live_data.get('othermatches', [])
                            league_games = []
                            for game in other_games[:10]:  # Limit to 10 other games
                                league_games.append({
                                    "id": game.get('id', ''),
                                    "team1_name": game.get('team1Name', ''),
                                    "team2_name": game.get('team2Name', ''),
                                    "team1_score": game.get('team1Score', 0),
                                    "team2_score": game.get('team2Score', 0),
                                    "period": game.get('period', 0),
                                    "clock": game.get('clock', '00:00'),
                                    "team1_logo": game.get('team1', {}).get('logoS', {}).get('url', ''),
                                    "team2_logo": game.get('team2', {}).get('logoS', {}).get('url', '')
                                })
                            
                            live_scores_data[game_id] = {
                                # Basic game info
                                "team1_name": tm1.get('name', ''),
                                "team2_name": tm2.get('name', ''),
                                "team1_code": tm1.get('code', ''),
                                "team2_code": tm2.get('code', ''),
                                "team1_score": tm1.get('score', 0),
                                "team2_score": tm2.get('score', 0),
                                "clock": live_data.get('clock', '00:00'),
                                "period": live_data.get('period', 0),
                                "period_type": live_data.get('periodType', ''),
                                "period_length": live_data.get('periodLength', 10),
                                "in_ot": live_data.get('inOT', 0),
                                "match_id": match_id,
                                
                                # Visual assets
                                "team1_logo": tm1.get('logoS', {}).get('url', ''),
                                "team2_logo": tm2.get('logoS', {}).get('url', ''),
                                
                                # Team statistics
                                "team1_stats": {
                                    "field_goal_percentage": tm1.get('tot_sFieldGoalsPercentage', 0),
                                    "three_point_percentage": tm1.get('tot_sThreePointersPercentage', 0),
                                    "free_throw_percentage": tm1.get('tot_sFreeThrowsPercentage', 0),
                                    "rebounds": tm1.get('tot_sReboundsTotal', 0),
                                    "assists": tm1.get('tot_sAssists', 0),
                                    "turnovers": tm1.get('tot_sTurnovers', 0),
                                    "steals": tm1.get('tot_sSteals', 0),
                                    "blocks": tm1.get('tot_sBlocks', 0),
                                    "bench_points": tm1.get('tot_sBenchPoints', 0),
                                    "points_in_paint": tm1.get('tot_sPointsInThePaint', 0),
                                    "points_from_turnovers": tm1.get('tot_sPointsFromTurnovers', 0),
                                    "fast_break_points": tm1.get('tot_sPointsFastBreak', 0),
                                    "biggest_lead": tm1.get('tot_sBiggestLead', 0),
                                    "time_leading": tm1.get('tot_sTimeLeading', 0)
                                },
                                "team2_stats": {
                                    "field_goal_percentage": tm2.get('tot_sFieldGoalsPercentage', 0),
                                    "three_point_percentage": tm2.get('tot_sThreePointersPercentage', 0),
                                    "free_throw_percentage": tm2.get('tot_sFreeThrowsPercentage', 0),
                                    "rebounds": tm2.get('tot_sReboundsTotal', 0),
                                    "assists": tm2.get('tot_sAssists', 0),
                                    "turnovers": tm2.get('tot_sTurnovers', 0),
                                    "steals": tm2.get('tot_sSteals', 0),
                                    "blocks": tm2.get('tot_sBlocks', 0),
                                    "bench_points": tm2.get('tot_sBenchPoints', 0),
                                    "points_in_paint": tm2.get('tot_sPointsInThePaint', 0),
                                    "points_from_turnovers": tm2.get('tot_sPointsFromTurnovers', 0),
                                    "fast_break_points": tm2.get('tot_sPointsFastBreak', 0),
                                    "biggest_lead": tm2.get('tot_sBiggestLead', 0),
                                    "time_leading": tm2.get('tot_sTimeLeading', 0)
                                },
                                
                                # Player information (in the format the sensor expects)
                                "team1_players": team1_players,
                                "team2_players": team2_players,
                                
                                # Also provide the original tm structure for sensors that still use it
                                "tm": {
                                    "1": tm1,
                                    "2": tm2
                                },
                                
                                # Legacy top scorers list
                                "top_scorers": top_scorer_list,
                                
                                # Game officials
                                "officials": official_list,
                                
                                # League scoreboard
                                "other_games": league_games,
                                
                                # Coaching staff
                                "team1_coach": tm1.get('coach', ''),
                                "team2_coach": tm2.get('coach', '')
                            }
                            _LOGGER.debug(f"Updated comprehensive live data for game {game_id}: {tm1.get('name', 'Team1')} {tm1.get('score', 0)}-{tm2.get('score', 0)} {tm2.get('name', 'Team2')}")
                        
            except aiohttp.ClientError as err:
                _LOGGER.debug(f"HTTP error fetching live scores for match {match_id}: {err}")
            except asyncio.TimeoutError:
                _LOGGER.debug(f"Timeout error fetching live scores for match {match_id}")
            except Exception as err:
                _LOGGER.debug(f"Error fetching live scores for match {match_id}: {err}")
        
        if live_scores_data:
            # Update the coordinator data with live scores
            current_data = self.data or {}
            current_data["live_scores"] = live_scores_data
            self.async_set_updated_data(current_data)
            _LOGGER.info(f"Updated live scores for {len(live_scores_data)} games")
