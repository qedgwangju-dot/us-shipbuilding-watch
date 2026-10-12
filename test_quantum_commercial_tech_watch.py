#!/usr/bin/env python3
import quantum_commercial_tech_watch as q

def main():
    bad_title="IonQ's Order Book Fills Up as Wall Street Starts Paying Attention"
    bad_desc=(
        "BofA initiated coverage with a Buy rating and price target while recapping "
        "September 21 SDT, September 23 NVIDIA and September 24 FIU deployments."
    )
    assert not q.trusted_news_source("AD HOC NEWS")
    assert not q.material(f"{bad_title} {bad_desc}")

    google_item={
        "source":"Reuters",
        "title":"IonQ customer deployment recap",
        "url":"https://news.google.com/rss/articles/example",
        "published":"2026-10-03 14:10",
        "summary":"IonQ deployment commentary without a newly named counterparty.",
        "origin":"news",
    }
    assert q.article_text(google_item)==q.clean(google_item["summary"])
    assert q.partner_key("IonQ story carried on Google News")=="none"
    assert not q.candidate_is_specific(
        google_item,
        "IonQ deployment and orders are growing but no concrete new counterparty is named.",
    )

    fiu=(
        "IonQ contract with Florida International University FIU for deployment "
        "of a Superion 256 quantum computer."
    )
    assert q.partner_key(fiu)=="fiu"
    assert q.event_key({"title":"x"},fiu)==["IonQ|상용화·고객배치|fiu|superion256"]

    sdt=(
        "IonQ and SDT multi-year contract and supply agreement for Superion 256 "
        "customer deployment in South Korea."
    )
    assert q.partner_key(sdt)=="sdt"
    assert q.event_key({"title":"x"},sdt)==["IonQ|상용화·고객배치|sdt|superion256"]

    qec="IonQ quantum error correction 408 logical qubits 0.02% latency"
    assert q.material(qec)
    assert q.candidate_is_specific(
        {"origin":"news","source":"Reuters"},
        qec,
    )

    # Regression: official IonQ releases missed by the earlier commercial-keyword
    # filter must be actionable even though neither headline says "deployment".
    darpa_title = "IonQ Advances to Final Stage of DARPA’s Quantum Benchmarking Initiative"
    darpa_full = (
        "October 7, 2026 IonQ and DARPA have signed a Stage C agreement "
        "with a potential value of up to $300 million, with independent validation through 2029."
    )
    darpa_item = {
        "source": "IonQ 공식 뉴스",
        "title": darpa_title,
        "url": "https://www.ionq.com/news/ionq-advances-to-final-stage-of-darpas-quantum-benchmarking-initiative",
        "origin": "official",
    }
    assert q.material(darpa_title + " " + darpa_full)
    assert q.item_event_type(darpa_item, darpa_full) == "정부 기술검증·계약"
    assert q.candidate_is_specific(darpa_item, darpa_full)
    assert q.event_key(darpa_item, darpa_title + " " + darpa_full) == [
        "IonQ|정부 기술검증·계약|darpa|qbi-stage-c-2026-10-07"
    ]
    assert "DARPA" in q.title_ko(darpa_item, darpa_full)

    memory_title = "IonQ Demonstrates World-First Quantum Memory-Enhanced Interconnect for Distributed Quantum Applications"
    memory_full = (
        "October 9, 2026 IonQ achieved more than 1,000 entanglement "
        "connections per second between trapped ion qubit and quantum memory via interconnect."
    )
    memory_item = {
        "source": "IonQ 공식 뉴스",
        "title": memory_title,
        "url": "https://www.ionq.com/news/ionq-demonstrates-world-first-quantum-memory-enhanced-interconnect-for-distributed-quantum-applications",
        "origin": "official",
    }
    assert q.material(memory_title + " " + memory_full)
    assert q.item_event_type(memory_item, memory_full) == "양자연결·메모리 실증"
    assert q.candidate_is_specific(memory_item, memory_full)
    assert q.event_key(memory_item, memory_title + " " + memory_full) == [
        "IonQ|양자연결·메모리 실증|memory-interconnect|1khz-2026-10-09"
    ]
    assert "1,000" in q.title_ko(memory_item, memory_full)

    # A general company landing page can mention many past customers but may
    # never masquerade as a new release after site navigation changes.
    landing = {
        "origin": "official", "title": "IonQ Selected Customers and Partners",
        "url": "https://www.ionq.com/customers-and-partners",
    }
    assert not q.candidate_is_specific(
        landing, "IonQ selected by Nvidia customer SDT FIU for deployment."
    )
    assert q.title_ko({
        "title": "IonQ's Order Book Fills Up as Wall Street Starts Paying Attention",
        "source": "AD HOC NEWS",
    }, "IonQ customer deployment") == ""

    # Header/footer historical DARPA references may not overwrite the body of
    # an otherwise unrelated article.
    original_get = q.get
    try:
        q.get = lambda url: (
            "<html><header>IonQ DARPA Stage C past release</header>"
            "<main><h1>IonQ Quantum Memory-Enhanced Interconnect</h1>"
            "<p>October 9, 2026. IonQ achieved more than 1,000 connections per second.</p>"
            "</main><footer>September 2026 NVIDIA</footer></html>"
        )
        isolated = q.article_text(memory_item)
        assert "DARPA" not in isolated and "NVIDIA" not in isolated
        assert "1,000" in isolated
    finally:
        q.get = original_get

    print("quantum_commercial_tech_tests=passed")

if __name__=="__main__":
    main()
