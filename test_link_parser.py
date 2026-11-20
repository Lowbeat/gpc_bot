"""Unit tests for link_parser and bot functions."""
import pytest
from link_parser import extract_url, is_domain_allowed, _load_allowed_domains


def test_extract_url_basic():
    """Test extracting URL from plain text."""
    assert extract_url("https://example.com/page") == "https://example.com/page"
    assert extract_url("Check https://example.com/page now") == "https://example.com/page"


def test_extract_url_with_query():
    """Test extracting URL with query parameters."""
    url = "https://www.wildberries.ru/catalog/867653263/detail.aspx?targetUrl=MI"
    assert extract_url(f"Look at {url}") == url


def test_extract_url_ozon():
    """Test Ozon URL extraction."""
    url = "https://www.ozon.ru/product/svezhaya-nota-123456/"
    assert extract_url(f"See {url}") == url


def test_extract_url_yandex_market():
    """Test Yandex Market URL extraction."""
    url = "https://market.yandex.ru/card/test/12345?cpc=abc"
    assert extract_url(f"Link: {url}") == url


def test_extract_url_aliexpress():
    """Test AliExpress URL extraction."""
    url = "https://aliexpress.ru/item/1005013068124123.html?spm=abc"
    assert extract_url(f"Check {url}") == url


def test_extract_url_returns_none_for_no_url():
    """Test that None is returned when no URL found."""
    assert extract_url("Just some text without links") is None
    assert extract_url("") is None
    assert extract_url(None) is None


def test_load_allowed_domains():
    """Test loading allowed domains from JSON."""
    domains = _load_allowed_domains()
    assert "wildberries.ru" in domains
    assert "ozon.ru" in domains
    assert "market.yandex.ru" in domains
    assert "aliexpress.ru" in domains
    assert "ali.click" in domains


def test_is_domain_allowed():
    """Test domain allowance check."""
    assert is_domain_allowed("https://www.wildberries.ru/catalog/123") is True
    assert is_domain_allowed("https://ozon.ru/product/123") is True
    assert is_domain_allowed("https://market.yandex.ru/card/123") is True
    assert is_domain_allowed("https://aliexpress.ru/item/123") is True
    assert is_domain_allowed("https://ali.click/abc") is True
    assert is_domain_allowed("https://example.com/page") is False
    assert is_domain_allowed("https://google.com") is False


def test_is_domain_allowed_case_insensitive():
    """Test domain check is case insensitive."""
    assert is_domain_allowed("https://WWW.WILDBERRIES.RU/catalog/123") is True
    assert is_domain_allowed("https://Market.Yandex.Ru/card/123") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])