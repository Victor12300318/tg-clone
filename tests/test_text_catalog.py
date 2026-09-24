import pytest
from core.cloner_engine import apply_text_transformations


def test_preserve_catalog_text_without_links():
    # If text has no links (e.g. model name), it should be preserved for pure text messages even if remove_captions is set
    text = "Ercilia Micarelli"
    res = apply_text_transformations(text, remove_captions=True, remove_links=True, is_pure_text=True)
    assert res == "Ercilia Micarelli"


def test_clean_links_preserve_model_name():
    # If text has model name and a spam link, remove the link but keep the model name
    text = "Ercilia Micarelli https://t.me/spamchannel"
    res = apply_text_transformations(text, remove_captions=True, remove_links=True, is_pure_text=True)
    assert "Ercilia Micarelli" in res
    assert "https://" not in res
    assert "t.me" not in res


def test_strip_pure_link_when_remove_captions():
    # If text is ONLY a link and remove_captions is True, it should result in empty/None
    text = "https://t.me/onlylink"
    res = apply_text_transformations(text, remove_captions=True, remove_links=True, is_pure_text=True)
    assert res is None or res.strip() == ""

