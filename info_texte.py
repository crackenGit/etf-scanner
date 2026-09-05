"""
info_texte.py

Die großen, überwiegend statischen Erklär- und Statistik-Textblöcke der
App - ausgelagert, damit app.py nicht mit reinem Anzeige-Text überladen
wird. Jede Funktion rendert direkt (ruft st.markdown/st.caption selbst
auf) und wird von app.py an der passenden Stelle aufgerufen.

Teil des app.py-Refactors - reine Verschiebung, keine Verhaltensänderung.
"""

import streamlit as st

from dip_score import (
    KAUFSIGNAL_SCHWELLE,
    SOFT_KAUFSIGNAL_SCHWELLE,
    ZIEL_RENDITE_SOFT_PCT,
    ZIEL_RENDITE_VOLL_PCT,
    MARKT_BENCHMARK_TICKER,
)


def render_kaufsignal_erklaerung():
    with st.expander("ℹ️ Wann entsteht ein Kaufsignal? (Hier klicken)"):
        st.markdown(f"""
        ### 🎯 Zwei Signalstufen statt Ja/Nein
        Es gibt keinen separaten Ja/Nein-Filter - alle Kriterien (RSI, Trend,
        Kursrückgang, Marktumfeld) fließen in **einen einzigen Score** von
        maximal 100 Punkten ein. Der Backtest über ~277.000 ETF-Tage zeigt
        zwei sinnvolle Schwellen mit unterschiedlicher Renditeerwartung:

        | Stufe | Score | Backtest-Trefferquote (40 Handelstage) |
        |---|---|---|
        | 🟡 Softes Signal | {SOFT_KAUFSIGNAL_SCHWELLE:.0f}-{KAUFSIGNAL_SCHWELLE - 1:.0f} | ~69% erreichen +5% |
        | 🔥 Kaufsignal | ≥ {KAUFSIGNAL_SCHWELLE:.0f} | ~56% erreichen +10% |

        Bei der Order lohnt es sich, die Stufe zu notieren (z. B. in
        `portfolio.py`) - ein softes Signal rechtfertigt eher ein niedrigeres
        Ziel (~5%) als ein volles Kaufsignal (~10%).

        | Komponente | Max. Punkte | Was gemessen wird |
        |---|---|---|
        | RSI-Sweet-Spot | 20 | Abstand zu RSI 25 (siehe unten) |
        | Trend-Struktur | 15 | EMA50 **unter** GD200 **oder** GD200 fällt (siehe unten - Logik bewusst umgekehrt) |
        | Abstand zur GD200 | 15 | Distanz zur GD200, **Richtung egal** (siehe unten) |
        | Mean-Reversion-Potenzial | 20 | Rebound-Distanz bis zur EMA50 |
        | **Kursrückgang-Tiefe (ATR)** | **30** | Wie stark der Kurs vor dem Signal fiel, volatilitätsbereinigt |

        **Wichtig:** Ohne nennenswerten vorherigen Kursrückgang sind maximal
        **70** der 100 Punkte erreichbar (RSI + Trend + GD200-Abstand +
        EMA50-Potenzial). Ein Kaufsignal ab {KAUFSIGNAL_SCHWELLE:.0f} Punkten
        ist damit rechnerisch nur möglich, wenn der Kurs auch tatsächlich
        spürbar gefallen ist.

        ### 🎯 RSI-Sweet-Spot statt "je tiefer desto besser"
        Der Backtest zeigte einen Peak bei RSI 20-30 - RSI unter 20 performte
        **schlechter** (vermutlich eher Crash-Signal als normaler Dip). Der
        Score hat deshalb sein Maximum bei RSI 25 und fällt zu **beiden**
        Seiten linear ab, statt einfach mit sinkendem RSI immer weiter zu
        steigen.

        ### 🔄 Trend-Struktur & GD200-Abstand - Logik bewusst umgekehrt
        Ursprünglich belohnte der Score einen "intakten" Aufwärtstrend (EMA50
        über GD200, GD200 steigend) und einen möglichst großen Puffer *über*
        der GD200. Ein gezielter Nachtest (innerhalb gleicher Rückgangstiefe-
        Stufen, um einen Doppel-Zähl-Effekt auszuschließen) zeigte das
        **Gegenteil**: ETFs mit bereits **gebrochener** Trendstruktur
        schnitten in jeder geprüften Rückgangs-Kategorie besser ab (z. B. 33,9%
        vs. 21,8% Chance auf +10% bei starkem Rückgang) - vermutlich, weil ein
        gebrochener Trend einen "reiferen", weiter fortgeschrittenen Rückgang
        anzeigt, näher am Boden. Beim GD200-Abstand zeigte sich zusätzlich eine
        **U-Form** statt einer Linie: sowohl weit unter als auch weit über der
        GD200 schnitten deutlich besser ab als der ehemalige "Sweet Spot" bei
        +5 bis +10% (dem schwächsten Bereich überhaupt). Das Downside-Risiko
        wurde für beide Extreme geprüft und ist vergleichbar - keine
        Sonderbehandlung einer Richtung nötig. Bricht mit klassischer
        Chart-Weisheit, ist aber gut durch die Daten gestützt.

        ### 📉 Kursrückgang-Tiefe (ATR-normalisiert, größte Einzelkomponente)
        Rückgang vom 20-Tage-Hoch bis heute, gemessen in Vielfachen des
        ATR(14) statt in rohen Prozent. Ein roher Prozent-Rückgang war zwar das
        stärkste Einzelsignal, aber stark sektor-/volatilitätsverzerrt (rohe
        -30%-Rückgänge kommen fast nur bei volatilen Sektoren wie Halbleiter
        vor). Nach ATR-Normierung bleibt ein kleinerer, aber sauberer,
        sektor-fairer Effekt. Volle Punktzahl ab 6 ATR Rückgang.

        ### 🐂🐻 Marktphase (rein informativ, kein Einfluss auf den Score)
        Zusätzlich wird angezeigt, ob der breite Referenzindex (`{MARKT_BENCHMARK_TICKER}`)
        selbst über seinem GD200 notiert (Bulle) oder darunter (Bär), inklusive
        seit wie vielen Handelstagen diese Phase andauert. Früher wurden Scores
        in einer "Bär"-Phase mit ×0.8 gedämpft - ein gezielter Nachtest zeigte
        aber: die für uns relevante Zielrenditen-Quote war dabei nicht schlechter,
        teils sogar besser, und Ziele wurden im Schnitt sogar schneller erreicht.
        Der Malus wurde deshalb entfernt. Der Hinweis bleibt trotzdem sichtbar -
        einerseits als Kontext, andererseits: Ein deutlicher Phasenwechsel ist
        ein guter Anlass, die Formel neu zu backtesten, da sie auf die
        Marktbedingungen der bisherigen Historie kalibriert ist.
        """)


def render_verkaufsziel_erklaerung():
    with st.expander("📊 Wie wird das Verkaufsziel gesetzt? (Regelwerk)", expanded=False):
        col_t1, col_t2 = st.columns(2)

        with col_t1:
            st.markdown(f"""
            ### 🟡 Softes Signal → +{ZIEL_RENDITE_SOFT_PCT:.0f}%
            * **Ziel:** Kompletter Verkauf bei Kaufkurs + {ZIEL_RENDITE_SOFT_PCT:.0f}%.
            * **Sinn:** Backtest-optimiert - schlägt die alte EMA50-Regel in
              Rendite pro Tag deutlich (0,32 vs. 0,13 %/Tag).
            """)

        with col_t2:
            st.markdown(f"""
            ### 🔥 Volles Signal → +{ZIEL_RENDITE_VOLL_PCT:.0f}%
            * **Ziel:** Kompletter Verkauf bei Kaufkurs + {ZIEL_RENDITE_VOLL_PCT:.0f}%.
            * **Sinn:** Schlägt die alte EMA50/52W-Hoch-Regel auch absolut
              (+3,58% vs. +3,27%) bei weniger als halber Haltedauer.
            """)

        st.caption(
            "Ersetzt die frühere zweistufige EMA50/52-Wochen-Hoch-Tranchenlogik "
            "(Kompletteverkauf statt Teilverkauf) - siehe Portfolio-Tab für den "
            "aktuellen Abstand zum Ziel je Position. Damit ein Kursziel berechnet "
            "werden kann, muss die Position `'signal_stufe': 'soft'` oder "
            "`'voll'` (oder ersatzweise `'dip_score_bei_kauf'`) in `portfolio.py` haben."
        )


def render_offene_punkte(offene_punkte):
    with st.expander("📝 Offene Punkte & TODOs", expanded=False):
        st.caption(
            "Dinge, die wir im Blick behalten oder noch genauer untersuchen "
            "sollten - wird manuell gepflegt (siehe OFFENE_PUNKTE in app.py)."
        )
        for punkt in offene_punkte:
            st.markdown(f"**{punkt['status']} - {punkt['titel']}**")
            st.caption(punkt["kontext"])
            st.divider()


def render_statistik_tab():
    with st.expander("📈 Backtest-Erkenntnisse zum Nachlesen (Statistik)", expanded=False):
        st.caption(
            "Alle Zahlen aus dem eigenen Backtest (`backtest.py`), 277.000+ ETF-Tage, "
            "~18 Jahre Historie, Stand nach der ATR-Formel-Umstellung. **Cluster** = "
            "unabhängige Marktereignisse (zeitlich nah beieinanderliegende Signale "
            "über alle Ticker hinweg zählen als 1) - die eigentlich verlässliche "
            "Stichprobengröße, nicht die rohe Episoden-Zahl. Unter ~15-20 Clustern "
            "sind Zahlen eher eine Tendenz als ein Beweis."
        )

        st.markdown(f"""
        ##### 1) Warum die Schwellen {SOFT_KAUFSIGNAL_SCHWELLE:.0f} / {KAUFSIGNAL_SCHWELLE:.0f}?
        | Schwelle | Episoden/Cluster | Chance auf +5% | Chance auf +10% |
        |---|---|---|---|
        | 65 | 1.283/122 | 67,0% | 35,3% |
        | 70 | 545/83 | 75,0% | 41,7% |
        | **75 (voll)** | 212/39 | 76,9% | **51,9%** |
        | 80 | 88/21 | 78,4% | 50,0% |
        | 85 | 28/10 | 85,7% (dünn) | 53,6% (dünn) |

        75 bietet den besten Kompromiss aus Stichprobengröße und Trefferqualität -
        darüber wird die Stichprobe schnell zu dünn.
        """)

        st.markdown("""
        ##### 2) RSI-Sweet-Spot bei 25 (nicht "je tiefer desto besser")
        | RSI-Bereich | Trefferquote | Chance auf +10% |
        |---|---|---|
        | 0-20 | 56,9% | 37,6% |
        | **20-25** | 62,8% | **42,5%** |
        | 25-30 | 63,7% | 41,2% |
        | 30-35 | 63,3% | 37,0% |
        | 35-40 | 61,7% | 32,0% |

        RSI unter 20 performt schlechter, nicht besser - vermutlich eher
        Crash- als Dip-Signal.
        """)

        st.markdown("""
        ##### 3) Kursrückgang-Tiefe (roh, vor ATR-Normierung)
        | Rückgang | Trefferquote | Chance auf +10% |
        |---|---|---|
        | -30% und tiefer | 73,6% | 79,8% |
        | -20% bis -30% | 67,6% | 71,6% |
        | -10% bis -20% | 60,6% | 50,4% |
        | -5% bis -10% | 60,7% | 35,0% |
        | 0% bis -5% | 59,8% | 23,4% |

        Klar monoton - tieferer Rückgang = bessere Erholungschance. **ATR-normalisiert**
        (volatilitätsbereinigt) bleibt derselbe Effekt bestehen, aber deutlich
        schwächer (21,7% → 28,4% statt 23,4% → 79,8%) - ein großer Teil des rohen
        Effekts war Sektor-Bias (volatile Sektoren wie Halbleiter fielen öfter tief
        UND liefen in diesem Zeitraum ohnehin besser). Der Score nutzt deshalb die
        ATR-Version, nicht die rohe.
        """)

        st.markdown("""
        ##### 4) Trend-Struktur & GD200-Abstand - Logik umgekehrt (Update)
        | Trend-Kombination | Cluster | Chance auf +10% (bei starkem Rückgang) |
        |---|---|---|
        | EMA50>GD200 **und** GD200 steigt (alte "volle Punktzahl") | 183 | **21,8%** (schwächste) |
        | EMA50>GD200, GD200 fällt | 180 | 26,1% |
        | EMA50<GD200, GD200 steigt | 157 | 28,4% |
        | EMA50<GD200 **und** GD200 fällt (neue "volle Punktzahl") | 166 | **33,9%** (stärkste) |

        | GD200-Abstand | Chance auf +10% (bei starkem Rückgang) |
        |---|---|
        | Tief unter GD200 (<-5%) | **45,0%** |
        | Mitte (0-10%, ehemaliger "Sweet Spot") | 15-24% (schwächster Bereich) |
        | Weit drüber (≥20%) | **37,3%** |

        Beide Effekte wurden **innerhalb gleicher ATR-Rückgang-Stufen** geprüft
        (um auszuschließen, dass sie nur den Rückgang doppelt zählen) und blieben
        dort genauso stark oder stärker bestehen - also echte, unabhängige Signale.
        Downside-Risiko (max. Zwischenzeit-Rückgang) wurde für die GD200-Extreme
        geprüft und ist vergleichbar, keine Sonderbehandlung nötig. Bricht mit
        klassischer Chart-Weisheit ("kaufe nur im intakten Trend"), ist aber
        gut durch die Daten gestützt - siehe Chat für die vollständige Herleitung.
        """)

        st.markdown(f"""
        ##### 5) Exit-Ziele: feste Prozentrendite schlägt die alte EMA50-Regel
        | Signal | Alte EMA50-Regel | Festes Ziel (aktuell) |
        |---|---|---|
        | Soft ({SOFT_KAUFSIGNAL_SCHWELLE:.0f}-{KAUFSIGNAL_SCHWELLE-1:.0f}) | +1,99% in 25,4 Tagen | **+{ZIEL_RENDITE_SOFT_PCT:.0f}%-Ziel: 74,7% Erreichquote in 10,0 Tagen** |
        | Voll (≥{KAUFSIGNAL_SCHWELLE:.0f}) | +2,58% in 30,7 Tagen | **+{ZIEL_RENDITE_VOLL_PCT:.0f}%-Ziel: 66,5% Erreichquote in 13,4 Tagen** |

        Feste Ziele sind sowohl schneller als auch effizienter (Rendite/Tag) als
        die frühere Tranchenlogik.
        """)

        st.markdown("""
        ##### 6) Renditeprofil nach Haltedauer (unabhängig von jeder Exit-Regel)
        """)
        col_rp1, col_rp2 = st.columns(2)
        with col_rp1:
            st.markdown("""
            **Softes Signal** (1.374 Episoden / 117 Cluster)
            | Tag | Ø Rendite | Trefferquote |
            |---|---|---|
            | 1 | +0,21% | 55,5% |
            | 5 | +0,65% | 62,1% |
            | 10 | +0,95% | 65,9% |
            | 21 | +2,06% | 67,9% |
            | 40 | +3,30% | 68,0% |
            """)
        with col_rp2:
            st.markdown("""
            **Volles Signal** (212 Episoden / 39 Cluster)
            | Tag | Ø Rendite | Trefferquote |
            |---|---|---|
            | 1 | +0,25% | 57,5% |
            | 5 | +1,88% | 70,3% |
            | 10 | +2,12% | 75,0% |
            | 21 | +2,60% | 67,0% |
            | 40 | +4,49% | 69,1% |
            """)

        st.markdown("""
        ##### 7) Marktregime: Malus entfernt (Update)
        Bei **gleichem Rohscore** (vor der früheren Regime-Dämpfung zurückgerechnet),
        Bulle vs. Bär verglichen:

        | Rohscore | Chance auf +10% (Bulle vs. Bär) | Ø Tage bis Ziel (Bulle vs. Bär) |
        |---|---|---|
        | 30-45 | 20,9% vs. **31,2%** | 23,7 vs. **20,4** |
        | 45-60 | 24,9% vs. **29,5%** | 22,9 vs. **19,7** |
        | 60-75 | **30,4%** vs. 28,5% | **21,1** vs. 19,9 |
        | 75-100 | **51,9%** vs. 50,0% | 18,6 vs. 20,8 (dünn: n=10 Cluster) |

        Die für uns entscheidende Zielrenditen-Quote war bei schlechtem Regime nicht
        schlechter (meist sogar besser), und Ziele wurden im Schnitt eher schneller
        als langsamer erreicht - nur eine nicht handelsrelevante Nebenmetrik
        (Trefferquote nach fixen 21 Tagen) sprach für den alten ×0.8-Malus. Der
        Regime-Status bleibt als reine Information sichtbar (Bulle/Bär-Anzeige,
        inkl. Tage seit Phasenwechsel), beeinflusst den Score aber nicht mehr.
        """)

        st.markdown("""
        ##### 8) Jahres-Robustheit
        Trägt über die meisten Jahre der Historie (2009-2026), mit einer klaren
        Ausnahme: **2025 war das schwächste Jahr** (13,2% Trefferquote bei Schwelle
        75, n=38/5 Cluster) - kein Ausschlusskriterium, aber ein Hinweis, dass die
        Strategie nicht in jeder Marktphase gleich gut funktioniert.
        """)

        st.caption(
            "⚠️ Diese Zahlen sind ein Stand vom letzten Backtest-Lauf, kein Live-Update - "
            "nach der nächsten Formel-/Schwellen-Anpassung hier manuell nachziehen."
        )
