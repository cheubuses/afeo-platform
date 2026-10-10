import pystac_client

STAC_URL = "https://earth-search.aws.element84.com/v1"


def connect_to_catalog():
    catalog = pystac_client.Client.open(STAC_URL)
    return catalog