"""Known brand names start live tasks at their official root URL instead of a web search."""
from __future__ import annotations

import pytest

from backend import config, site_directory
from backend.planner_engine import compile_goal


@pytest.mark.parametrize("goal,url", [
    ("Search for mechanical keyboards on Flipkart", "https://www.flipkart.com"),
    ("Open ChatGPT and ask it to write a haiku about Alexa", "https://chatgpt.com"),
    ("go to github and search for fastapi", "https://github.com"),
    ("Find the cheapest iPhone 15 on amazon", "https://www.amazon.in"),
    ("search wikipedia for alan turing", "https://www.wikipedia.org"),
    ("LeetCode: open the two sum problem", "https://leetcode.com"),
    ("Open YouTube and search lofi music", "https://www.youtube.com"),
])
def test_brand_names_map_to_official_root_urls(goal, url):
    g = compile_goal(goal)
    assert g["scenario"] == "live_autopilot", g
    assert g["target_url"] == url and g["constraints"]["discovery"] is False
    assert g["constraints"]["inferred_site"]


def test_query_after_a_named_site_is_extracted():
    assert compile_goal("search wikipedia for alan turing")["constraints"]["search_query"] == "alan turing"
    assert compile_goal("search on github for fastapi and open the first result")["constraints"]["search_query"] == "fastapi"


def test_typed_domains_win_and_topics_are_not_destinations():
    assert compile_goal("Go to amazon.com and search for headphones")["target_url"] == "https://amazon.com"
    topic = compile_goal("Tell me about the Amazon rainforest and search for its size on the web")
    assert topic["target_url"] == config.AUTOPILOT_SEARCH_URL and topic["constraints"]["discovery"] is True
    assert site_directory.infer_site("Amazon rainforest facts") is None
    assert site_directory.infer_site("search for amazon stock price") is None


def test_built_in_workflows_are_not_hijacked_by_a_brand_mention():
    assert compile_goal("Alexa, ask WebPilot to analyze the Model Context Protocol GitHub repository")["scenario"] == \
        "github_repo_analysis"
    assert compile_goal("Check how many coding questions I solved this month and compare my progress with my "
                        "friend's public profile.")["scenario"] == "profile_comparison"
    assert compile_goal("Search for distributed systems books on Paperlane")["constraints"]["discovery"] is True


def test_directory_can_be_extended_from_the_environment(monkeypatch):
    monkeypatch.setenv("WEBPILOT_SITE_DIRECTORY_JSON", '{"trekmart": "https://trekmart.example", "bad": "javascript:alert(1)"}')
    assert site_directory.infer_site("search for tents on Trekmart") == ("trekmart", "https://trekmart.example")
    assert site_directory.lookup("bad") is None
    monkeypatch.setenv("WEBPILOT_SITE_DIRECTORY_JSON", "{not json")
    assert site_directory.lookup("github") == ("github", "https://github.com")
