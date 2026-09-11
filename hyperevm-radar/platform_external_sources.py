import requests
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


SOURCES = [
    ("HyperEco", "https://hypereco.io/"),
]

SKIP_DOMAINS = {
    "hypereco.io",
    "x.com",
    "twitter.com",
    "t.me",
    "telegram.me",
    "discord.com",
    "discord.gg",
    "github.com",
    "medium.com",
    "youtube.com",
    "youtu.be",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 HyperEVM-Platform-Radar/1.0"
}


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return

        attrs = dict(attrs)
        href = attrs.get("href")

        if href:
            self.links.append(href)


def host(url):
    try:
        h = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""

    if h.startswith("www."):
        h = h[4:]

    return h


def discover_external_links():
    found = {}

    for source_name, source_url in SOURCES:
        r = requests.get(
            source_url,
            timeout=20,
            headers=HEADERS,
        )
        r.raise_for_status()

        parser = LinkParser()
        parser.feed(r.text)

        for href in parser.links:
            url = urljoin(source_url, href)

            if not url.startswith(("http://", "https://")):
                continue

            domain = host(url)

            if not domain:
                continue

            if domain in SKIP_DOMAINS:
                continue

            found.setdefault(domain, {
                "domain": domain,
                "url": url,
                "source": source_name,
            })

    return list(found.values())


if __name__ == "__main__":
    rows = discover_external_links()

    print("external project links =", len(rows))
    print()

    for row in rows:
        print(
            row["source"],
            "|",
            row["domain"],
            "|",
            row["url"],
        )
