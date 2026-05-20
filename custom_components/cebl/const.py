"""Constants for the CEBL integration."""

DOMAIN = "cebl"
PLATFORMS = ["sensor"]
STARTUP_MESSAGE = "Starting CEBL integration"

# New API URLs
API_URL_FIXTURES = "https://api.data.cebl.ca/games/active/"
API_URL_LIVE_BASE = "https://fibalivestats.dcd.shared.geniussports.com/data/competition/"

# Required headers for CEBL API
API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Referer': 'https://cebl-stats-hub.web.app/',
    'X-Api-Key': '800chyzv2hvur3z0ogh39cve2zok0c',
    'Origin': 'https://cebl-stats-hub.web.app',
    'Connection': 'keep-alive',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
    'TE': 'trailers'
}
