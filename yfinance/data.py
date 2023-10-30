import functools
from functools import lru_cache

import logging

import requests as requests
from bs4 import BeautifulSoup
import re
import random
import time

from frozendict import frozendict

from . import utils

cache_maxsize = 64


def lru_cache_freezeargs(func):
    """
    Decorator transforms mutable dictionary and list arguments into immutable types
    Needed so lru_cache can cache method calls what has dict or list arguments.
    """

    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        args = tuple([frozendict(arg) if isinstance(arg, dict) else arg for arg in args])
        kwargs = {k: frozendict(v) if isinstance(v, dict) else v for k, v in kwargs.items()}
        args = tuple([tuple(arg) if isinstance(arg, list) else arg for arg in args])
        kwargs = {k: tuple(v) if isinstance(v, list) else v for k, v in kwargs.items()}
        return func(*args, **kwargs)

    # copy over the lru_cache extra methods to this wrapper to be able to access them
    # after this decorator has been applied
    wrapped.cache_info = func.cache_info
    wrapped.cache_clear = func.cache_clear
    return wrapped


class TickerData:
    """
    Have one place to retrieve data from Yahoo API in order to ease caching and speed up operations

    Credit for code for cookie & crumb goes to StackOverflow:
    https://stackoverflow.com/questions/76065035/yahoo-finance-v7-api-now-requiring-cookies-python
    """
    user_agent_headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.2171.95 Safari/537.36'}

    def __init__(self, ticker: str, session=None):
        self.ticker = ticker
        self._session = session or requests

    def _get_cookie(self, proxy=None, timeout=30):
        if utils.cookie is not None:
            return utils.cookie

        utils.get_yf_logger().debug(f"Fetching cookie ...")

        # To avoid infinite recursion, do NOT use self.get()
        response = self._session.get(
            url='https://fc.yahoo.com',
            headers=self.user_agent_headers,
            proxies=proxy,
            timeout=timeout)

        if not response.cookies:
            raise Exception("Failed to obtain Yahoo auth cookie.")

        utils.cookie = list(response.cookies)[0]
        utils.get_yf_logger().debug(f"cookie = '{utils.cookie}'")
        return utils.cookie

    def _get_crumb(self, proxy=None, timeout=30):
        if utils.crumb is not None:
            return utils.crumb

        utils.get_yf_logger().debug(f"Fetching crumb ...")

        cookie = self._get_cookie()
        crumb_response = self._session.get(
                    url="https://query1.finance.yahoo.com/v1/test/getcrumb",
                    headers=self.user_agent_headers,
                    cookies={cookie.name: cookie.value},
            proxies=proxy,
            timeout=timeout)
        utils.crumb = crumb_response.text
        if utils.crumb is None or '<html>' in utils.crumb:
            raise Exception("Failed to fetch crumb")

        utils.get_yf_logger().debug(f"crumb = '{utils.crumb}'")
        utils.crumb = utils.crumb
        return utils.crumb

    def _get_crumb_botunit(self, proxy=None, timeout=30):
        # Credit goes to @bot-unit #1729

        if utils.crumb is not None:
            return utils.crumb

        utils.get_yf_logger().debug(f"Fetching crumb ...")

        # ToDo: might have to force fetch crumb direct from `requests`, 
        # to avoid using cached crumb from `requests_cache`

        response = self._session.get('https://guce.yahoo.com/consent', headers=self.user_agent_headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        csrfTokenInput = soup.find('input', attrs={'name': 'csrfToken'})
        csrfToken = csrfTokenInput['value']
        sessionIdInput = soup.find('input', attrs={'name': 'sessionId'})
        sessionId = sessionIdInput['value']
        originalDoneUrl = 'https://finance.yahoo.com/'
        namespace = 'yahoo'
        data = {
            'agree': ['agree', 'agree'],
            'consentUUID': 'default',
            'sessionId': sessionId,
            'csrfToken': csrfToken,
            'originalDoneUrl': originalDoneUrl,
            'namespace': namespace,
        }
        self._session.post(f'https://consent.yahoo.com/v2/collectConsent?sessionId={sessionId}', data=data, headers=self.user_agent_headers)
        self._session.get(f'https://guce.yahoo.com/copyConsent?sessionId={sessionId}', headers=self.user_agent_headers)
        r = self._session.get('https://query2.finance.yahoo.com/v1/test/getcrumb', headers=self.user_agent_headers)
        utils.crumb = r.text

        if utils.crumb is None or '<html>' in utils.crumb:
            raise Exception("Failed to fetch crumb")

        utils.get_yf_logger().debug(f"crumb = '{utils.crumb}'")
        utils.crumb = utils.crumb
        return utils.crumb

    def get(self, url, user_agent_headers=None, params=None, cookies=None, proxy=None, timeout=30):
        utils.get_yf_logger().debug(f'get(): {url}')
        proxy = self._get_proxy(proxy)

        # Add cookie & crumb
        # if cookies is None:
        #     cookie = self._get_cookie()
        #     cookies = {cookie.name: cookie.value}
        # Update: don't need cookie
        if params is None:
            params = {}
        if 'crumb' not in params:
            # params['crumb'] = self._get_crumb()
            params['crumb'] = self._get_crumb_botunit()

        response = self._session.get(
            url=url,
            params=params,
            cookies=cookies,
            proxies=proxy,
            timeout=timeout,
            headers=user_agent_headers or self.user_agent_headers)
        return response

    @lru_cache_freezeargs
    @lru_cache(maxsize=cache_maxsize)
    def cache_get(self, url, user_agent_headers=None, params=None, proxy=None, timeout=30):
        return self.get(url, user_agent_headers, params, proxy, timeout)

    def _get_proxy(self, proxy):
        # setup proxy in requests format
        if proxy is not None:
            if isinstance(proxy, (dict, frozendict)) and "https" in proxy:
                proxy = proxy["https"]
            proxy = {"https": proxy}
        return proxy

    def get_raw_json(self, url, user_agent_headers=None, params=None, proxy=None, timeout=30):
        response = self.get(url, user_agent_headers=user_agent_headers, params=params, proxy=proxy, timeout=timeout)
        response.raise_for_status()
        return response.json()
