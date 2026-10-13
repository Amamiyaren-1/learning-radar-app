"""Fixture browser clients for existing HTTP regressions; no auth bypass."""
import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request

from learnradar.auth import FIXTURE_USERNAME, FIXTURE_PASSWORD, password_hash
from learnradar.auth_store import AuthStore


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def opener(*, redirects=False):
    handlers = [urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
    if not redirects:
        handlers.append(NoRedirect())
    return urllib.request.build_opener(*handlers)


def csrf_from(document):
    return re.search(r'name="csrf_token" value="([^"]+)"', document)[1]


def login(client, base):
    try:
        with client.open(base+'/login', timeout=5) as response:
            document = response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        if exc.code == 303:
            return
        raise
    fields = {'username': FIXTURE_USERNAME, 'password': FIXTURE_PASSWORD, 'csrf_token': csrf_from(document)}
    request = urllib.request.Request(base+'/login', data=urllib.parse.urlencode(fields).encode(), headers={'Origin': base, 'Sec-Fetch-Site': 'same-origin'})
    try:
        client.open(request, timeout=5).close()
    except urllib.error.HTTPError as exc:
        if exc.code != 303:
            raise
        exc.close()


def csrf(client, base):
    with client.open(base+'/', timeout=5) as response:
        return csrf_from(response.read().decode('utf-8'))


def initialize_account(path):
    AuthStore(path).set_account(FIXTURE_USERNAME, password_hash(FIXTURE_PASSWORD), fixture=False)
