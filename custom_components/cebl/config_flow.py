"""Config flow for CEBL integration."""
from homeassistant import config_entries
import voluptuous as vol
import aiohttp
import async_timeout
import asyncio
import logging

from .const import DOMAIN, API_URL_FIXTURES, API_HEADERS

_LOGGER = logging.getLogger(__name__)

class CEBLConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CEBL."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            team_name = user_input["team"]
            team = self.team_options_reverse[team_name]
            team_id = str(team["id"])
            return self.async_create_entry(
                title=f"CEBL - {team_name}",
                data={"teams": [team_id], "team_names": {team_id: team_name}},
            )

        # Fetch teams dynamically
        teams = await self._fetch_teams()
        if teams is None:
            errors["base"] = "cannot_connect"
            teams = []

        # Sort teams alphabetically by name
        teams.sort(key=lambda team: team["name"])

        self.team_options = {str(team["id"]): team["name"] for team in teams}  # Ensure team IDs are strings
        self.team_options_reverse = {team["name"]: team for team in teams}  # Reverse map for lookups

        schema = vol.Schema({
            vol.Required("team"): vol.In(list(self.team_options.values())),  # Correctly handle team options
        })

        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def _fetch_teams(self):
        """Fetch the list of teams from the new CEBL API."""
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.get(API_URL_FIXTURES, headers=API_HEADERS) as response:
                        if response.status != 200:
                            _LOGGER.error("Failed to fetch games: %s", response.status)
                            return None
                        
                        games = await response.json()
                        teams = {}  # Use dict to avoid duplicates
                        
                        for game in games:
                            # Add home team
                            home_team_id = game.get("home_team_id")
                            home_team_name = game.get("home_team_name")
                            if home_team_id and home_team_name:
                                teams[home_team_id] = {
                                    "id": home_team_id,
                                    "name": home_team_name
                                }
                            
                            # Add away team
                            away_team_id = game.get("away_team_id")
                            away_team_name = game.get("away_team_name")
                            if away_team_id and away_team_name:
                                teams[away_team_id] = {
                                    "id": away_team_id,
                                    "name": away_team_name
                                }
                        
                        # Convert dict values to list
                        teams_list = list(teams.values())
                        _LOGGER.info(f"Fetched {len(teams_list)} teams from CEBL API")
                        return teams_list
                        
        except aiohttp.ClientError as err:
            _LOGGER.error("HTTP error fetching teams: %s", err)
        except asyncio.TimeoutError:
            _LOGGER.error("Timeout error fetching teams")
        except Exception as err:
            _LOGGER.error("Unexpected error fetching teams: %s", err)
        return None

    async def async_step_import(self, user_input=None):
        """Handle import from configuration.yaml."""
        return await self.async_step_user(user_input)
