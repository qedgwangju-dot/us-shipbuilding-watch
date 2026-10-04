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

    print("quantum_commercial_tech_tests=passed")

if __name__=="__main__":
    main()
