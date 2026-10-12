#!/usr/bin/env python3
"""Fail-closed regression tests for Genesis Mission science execution alert."""
import datetime as dt
import json
import os
os.environ["GENESIS_TEST_MODE"]="1"  # Mock message_id values must never resemble live proof.
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import requests
import genesis_science_execution_watch as g

def fixtures():
    return {
        "url":"https://www.energy.gov/articles/genesis-mission-new-project-grants",
        "title":"DOE Announces New Grants for Genesis Mission Quantum Research at Harvard University",
        "published":"2026-10-15",
        "body":"The Department of Energy selected Harvard University for a new Genesis Mission quantum research project. The grant award funds quantum error correction research at the laboratory and requires milestones. " * 2,
    }

def main():
    assert g.official_host("https://www.energy.gov/articles/test")
    assert g.official_host("https://investor.nvidia.com/news/test")
    assert not g.official_host("https://www.energy.gov.evil.example/articles/test")
    assert not g.official_host("https://news.google.com/rss/articles/abc")
    assert g.date_parse("October 8, 2026")==dt.date(2026,10,8)
    assert g.date_parse("2026-10-08T10:00:00Z")==dt.date(2026,10,8)
    from bs4 import BeautifulSoup
    soup=BeautifulSoup('<html><meta property="article:published_time" content="2026-10-08T12:00:00Z"><article><h1>NVIDIA</h1></article></html>',"html.parser")
    assert g.primary_date(soup)==dt.date(2026,10,8)

    for url,event in g.DIRECT_BASELINE_SOURCES.items():
        assert event in g.BASELINE
        item={"url":url,"title":"Known Genesis Mission base announcement","published":"2026-10-12","body":"NVIDIA Genesis Mission commitments"}
        assert g.is_baseline(item)
        assert g.classification(item) is None

    # DOE's own mission news listing is a first-party discovery path, not
    # Google News RSS. Reject off-site and non-article links.
    doe_index_html=(
        '<html><main><a href="/articles/new-genesis-mission-harvard-quantum-grant">'
        'DOE Announces a New Harvard Quantum Grant</a>'
        '<a href="https://evil.example/articles/fake-award">Impostor</a>'
        '<a href="/undersecretaryforscience/genesis-mission">Mission home</a>'
        '</main></html>'
    )
    with patch.object(g,"get",return_value=doe_index_html):
        doe_links=g.fetch_doe_genesis_index()
        assert doe_links == ["https://www.energy.gov/articles/new-genesis-mission-harvard-quantum-grant"],doe_links

    # The actual first-party NVIDIA RSS must carry the original announcement
    # date. Do not trust a 12 Oct Google News wrapper as the source date.
    rss_fixture='''<rss><channel>
      <item><title>NVIDIA Commits $1 Billion to Advance US Science Over the Next Five Years</title>
      <link>https://nvidianews.nvidia.com/news/nvidia-commits-1-billion-to-advance-us-science-over-the-next-five-years</link>
      <pubDate>Thu, 08 Oct 2026 10:00:00 GMT</pubDate>
      <description>NVIDIA Genesis Mission science and quantum research commitments, including cloud computing credits.</description></item>
      <item><title>Fake unsupported item</title>
      <link>https://news.google.com/rss/articles/xyz</link>
      <pubDate>Thu, 08 Oct 2026 10:00:00 GMT</pubDate></item>
      </channel></rss>'''
    with patch.object(g,"get",return_value=rss_fixture):
        official_rss=g.fetch_nvidia_press_rss()
        assert len(official_rss)==1
        item=next(iter(official_rss.values()))
        assert item["published"]=="2026-10-08"
        assert g.is_baseline(item)

    # A new publication date or a new URL cannot revive the October 8 pledge.
    recap={
        "url":"https://nvidianews.nvidia.com/news/another-copy",
        "title":"NVIDIA Commits $1 Billion to Advance US Science Over the Next Five Years",
        "published":"2026-10-12",
        "body":"NVIDIA Genesis Mission quantum computing support and research infrastructure."
    }
    assert g.is_baseline(recap)
    assert g.classification(recap) is None

    # The eleven commitments sum to $2.4bn; NVIDIA's $1bn is a COMPONENT,
    # not a fresh addition. DOE's $159m project awards are a separate bucket.
    commitments=list(map(Decimal,["1.0","0.5","0.2","0.15","0.15","0.1","0.1","0.05","0.05","0.05","0.05"]))
    assert len(commitments)==11
    assert sum(commitments)==Decimal("2.40")
    assert g.won("$1 billion",1400)=="약 1조4,000억원"
    assert g.won("$159 million",1400)=="약 2,226억원"

    # Require a NEW, specific execution milestone and verified primary date.
    with patch.object(g,"now_kst",return_value=dt.datetime(2026,10,20,tzinfo=g.KST)):
        new=g.classification(fixtures())
        assert new is not None
        assert new["stage"]=="신규 선정·자금 배정"
        assert "양자컴퓨팅" in new["area"]
        assert "금액 미공개" in g.render(new)
        assert "NVIDIA 10억달러" in g.render(new)
        no_action={**fixtures(),"title":"Genesis Mission Quantum Computing Outlook and Market Forecast"}
        assert g.classification(no_action) is None
        no_recipient={**fixtures(),"title":"DOE Announces New Grants for Genesis Mission Quantum Research","body":"Genesis Mission quantum computing experiment only, with no confirmed institution, award, contract or supplier."}
        assert g.classification(no_recipient) is None
        uncertified={**fixtures(),"url":"https://news.google.com/rss/articles/repost"}
        assert g.classification(uncertified) is None
        no_event_date={**fixtures(),"published":"2026-10-08"}
        assert g.classification(no_event_date) is None
        # NVIDIA repeating its own research pledge without a newly named
        # customer or award recipient is not a real new execution milestone.
        self_promotion={
            "url":"https://nvidianews.nvidia.com/news/genesis-promises",
            "title":"NVIDIA Expands Commitment to Genesis Mission Science",
            "published":"2026-10-15",
            "body":"NVIDIA expands its commitment and supports the Genesis Mission research project with quantum computing infrastructure."*3,
        }
        assert g.classification(self_promotion) is None
        contract={
            "url":"https://nvidianews.nvidia.com/news/nvidia-genesis-contract-argonne",
            "title":"NVIDIA Signs Contract to Deliver Genesis Mission GPU Systems to Argonne",
            "published":"2026-10-15",
            "body":"NVIDIA has signed a contract with Argonne National Laboratory for the Genesis Mission research project to deliver and deploy computing resources." * 2,
        }
        assert g.classification(contract)["stage"]=="계약·발주"

        # The 8 Oct $1bn pledge is not a reason to suppress a DIFFERENT
        # later signed NVIDIA/Argonne equipment contract.
        later_contract={
            **contract,
            "title":"NVIDIA Invests $1 Billion in Science and Signs Contract With Argonne for Genesis Mission",
        }
        assert not g.is_baseline(later_contract)
        assert g.classification(later_contract)["stage"]=="계약·발주"

        # Previously missed: a DOE follow-on award can name Harvard in the
        # title and identify the Genesis Mission only in the verified lead.
        # Its publication day must still be new and its recipient must be named.
        followup={
            "url":"https://www.energy.gov/articles/award-harvard-quantum-error-correction",
            "title":"DOE Announces New Grants for Quantum Error Correction at Harvard University",
            "published":"2026-10-15",
            "body":"As part of the Genesis Mission, DOE has selected Harvard University "
                   "for a new quantum error correction research award with test "
                   "milestones and project funding. " * 3,
        }
        followup_event=g.classification(followup)
        assert followup_event and followup_event["stage"]=="신규 선정·자금 배정"
        assert "양자컴퓨팅" in followup_event["area"]

        generic={**followup,
            "title":"DOE Announces New Grants for Quantum Research at Universities",
            "body":"As part of the Genesis Mission, DOE will support university and "
                   "laboratory research but has not named any recipient." * 3,
        }
        assert g.classification(generic) is None
        # "MIT" must be a standalone institution, not the substring in
        # "commitment".  No real recipient is named in this release.
        substring_false_positive={
            **followup,
            "title":"DOE Announces New Grants for Genesis Mission Quantum Research Commitment",
            "body":"The Genesis Mission commitment supports scientific work. "
                   "No named institution has an executed grant award. " * 3,
        }
        assert g.classification(substring_false_positive) is None

        unrelated={**followup,
            "body":("This unrelated research grant supports Harvard University. " * 50)
                   +"Elsewhere on this site: Genesis Mission upcoming news.",
        }
        assert g.classification(unrelated) is None
        unrelated_source={**followup,
            "url":"https://www.energy.gov.evil.example/articles/award-harvard",
        }
        assert g.classification(unrelated_source) is None

        # A damaged historical seen-state must never be treated as an empty
        # state, which could replay previously delivered real alerts.
        with tempfile.TemporaryDirectory() as directory:
            original_state=g.STATE
            try:
                g.STATE=Path(directory)/"damaged.json"
                g.STATE.write_text('{"seen": [',encoding="utf-8")
                with patch.object(g,"send",side_effect=AssertionError("must not send when state is damaged")):
                    try:
                        g.load_state()
                    except RuntimeError as exc:
                        assert "상태파일" in str(exc)
                    else:
                        raise AssertionError("corrupt Genesis state should block alerting")
            finally:
                g.STATE=original_state

        # Even if the Oct 8 releases stay accessible, an empty primary DOE
        # listing AND empty Google search must raise instead of claiming 0 news.
        with tempfile.TemporaryDirectory() as directory:
            original_state=g.STATE
            try:
                g.STATE=Path(directory)/"coverage-state.json"
                broken_health={
                    "baseline_verified":1,
                    "feed_items":0,
                    "doe_listing_count":0,
                    "source_errors":["DOE index returned zero"],
                }
                with patch.object(g,"discover",return_value=([],broken_health,{})), \
                     patch.object(g,"send",side_effect=AssertionError("coverage gap must never send")):
                    try:
                        g.main()
                    except RuntimeError as exc:
                        assert "수집 공백" in str(exc)
                    else:
                        raise AssertionError("dual-source gap must fail closed")
            finally:
                g.STATE=original_state

        # A successful, acknowledged send persists message_id and prevents repeat.
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)
            origin_state,origin_delivery=g.STATE,g.DELIVERY
            g.STATE=target/"state.json";g.DELIVERY=target/"delivery.json"
            try:
                with patch.object(g,"discover",return_value=([fixtures()["url"]],{"baseline_verified":1,"nvidia_verified":1,"feed_items":1,"decoded_official":1,"source_errors":[]},{})),patch.object(g,"article",return_value=fixtures()),patch.object(g,"quote_fx",return_value=(1400.0,"2026-10-19")):
                    sent=[]
                    with patch.object(g,"send",side_effect=lambda msg: sent.append(msg) or 987):
                        assert g.main()==0
                        assert len(sent)==1
                        s=json.loads(g.STATE.read_text())
                        assert len(s["seen"])==4 and s["seen"][new["key"]]["message_id"]==987
                        assert json.loads(g.DELIVERY.read_text())["receipts"][0]["message_id"]==987
                        assert g.main()==0
                        assert len(sent)==1
            finally:g.STATE,g.DELIVERY=origin_state,origin_delivery

        # An ambiguous HTTP timeout must not trigger repeated Telegram sends;
        # missing secrets/definite rejections must remain retryable.
        with tempfile.TemporaryDirectory() as directory:
            origin_state,origin_delivery=g.STATE,g.DELIVERY
            g.STATE=Path(directory)/"state.json";g.DELIVERY=Path(directory)/"delivery.json"
            try:
                with patch.object(g,"discover",return_value=([fixtures()["url"]],{"baseline_verified":1,"nvidia_verified":1,"feed_items":1,"decoded_official":1,"source_errors":[]},{})),patch.object(g,"article",return_value=fixtures()):
                    with patch.object(g,"send",side_effect=requests.exceptions.ReadTimeout("mock accepted?")):
                        try:g.main()
                        except requests.exceptions.ReadTimeout:pass
                        else:raise AssertionError("Timeout should fail run")
                    s=json.loads(g.STATE.read_text())
                    assert new["key"] in s["uncertain"] and new["key"] not in s["seen"]
                    with patch.object(g,"send",side_effect=AssertionError("re-sent quarantined event")):
                        assert g.main()==0
            finally:g.STATE,g.DELIVERY=origin_state,origin_delivery

    print("genesis_science_execution_tests=passed")

if __name__=="__main__":
    main()
