"""WolvWealth API Init."""

import wolvwealth
from wolvwealth.api.state import ApplicationState
import wolvwealth.api.optimize
import wolvwealth.api.main
import wolvwealth.api.api_exceptions
import wolvwealth.api.auth
import wolvwealth.model

state = ApplicationState()  # Preload global resources on startup
