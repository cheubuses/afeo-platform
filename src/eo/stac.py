"""Earth Search STAC catalog connection."""

import pystac_client
from pystac_client.stac_api_io import StacApiIO
from urllib3.util.retry import Retry

STAC_URL = "https://earth-search.aws.element84.com/v1"

# (connect, read) seconds. Large searches can be slow to answer, so the read
# timeout is generous, but it is finite so a stalled connection cannot hang.
DEFAULT_TIMEOUT = (10, 120)


def connect_to_catalog(
    url=STAC_URL,
    timeout=DEFAULT_TIMEOUT,
    retries=6,
):
    """
    Open the STAC API with a retry policy suited to long searches.

    The default pystac_client retry does not retry POST requests on server
    errors, and STAC searches are POSTs. Every STAC call here is read-only,
    so retrying all methods is safe.

    Requires pystac-client >= 0.7.
    """

    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=1.0,  # waits roughly 1, 2, 4, 8 ... seconds
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,  # retry POST as well as GET
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    stac_io = StacApiIO(timeout=timeout, max_retries=retry)

    return pystac_client.Client.open(url, stac_io=stac_io)