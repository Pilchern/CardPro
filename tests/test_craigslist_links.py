from src import craigslist_links


def test_search_url_builds_expected_format():
    url = craigslist_links.search_url("Michael Jordan card", "chicago", "sss")
    assert url == "https://chicago.craigslist.org/search/sss?query=Michael+Jordan+card"


def test_search_url_uses_given_site_and_category():
    url = craigslist_links.search_url("cards", "newyork", "clo")
    assert url.startswith("https://newyork.craigslist.org/search/clo?")
