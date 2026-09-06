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
            "~18 Jahre Historie, Stand nach der ATR-Formel-Umstellung."
        )

        st.markdown("""
        ##### 📖 Glossar
        | Begriff | Bedeutung |
        |---|---|
        | **ATR** (Average True Range) | Durchschnittliche Tagesschwankung der letzten 14 Tage - misst, wie volatil ein ETF normalerweise ist |
        | **ATR-Vielfaches** | Wie viele "ATRs" der Kurs unter seinem 20-Tage-Hoch liegt - macht Rückgänge zwischen ruhigen und volatilen ETFs vergleichbar |
        | **GD200** | 200-Tage-Durchschnittskurs - langfristiger Trendindikator |
        | **EMA50** | Exponentiell gewichteter 50-Tage-Durchschnitt - reagiert schneller auf aktuelle Kursbewegung als die GD200 |
        | **RSI** (Relative Strength Index) | Momentum-Indikator (0-100); niedrige Werte deuten auf "überverkauft" hin |
        | **Episode** | Ein zusammenhängender Zeitraum, in dem *ein* ETF ein Signal zeigt - mehrere Tage in Folge zählen als 1 |
        | **Cluster** | Episoden *verschiedener* ETFs, die zeitlich nah beieinander liegen, zu einem Marktereignis zusammengefasst - die eigentlich unabhängige Stichprobengröße, nicht die rohe Episoden-Zahl. Unter ~15-20 Clustern: Tendenz, kein Beweis |
        | **Dip Score / Rohscore** | Der berechnete Gesamt-Score (0-100); "Rohscore" = vor eventuellen Multiplikatoren |
        | **Trefferquote** | Anteil der Fälle mit positiver Rendite nach fixer Haltedauer (meist 21 Tage) |
        | **quote_Xpct** | Anteil der Fälle, die +X% *irgendwann* in 40 Handelstagen erreicht haben - unsere wichtigste Erfolgsmetrik |
        | **Signal-Stufe (soft/voll)** | Zwei Stärkegrade des Kaufsignals mit unterschiedlichem Renditeziel (4% / 7%) |
        | **Basisrate** | Durchschnittliche Erfolgsquote über die gesamte betrachtete Gruppe - Vergleichsmaßstab für die einzelnen Bereiche |
        """)

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
        | Jahr | Trefferquote | Cluster |
        |---|---|---|
        | 2010 | 82,9% | 7 |
        | 2019 | 78,7% | 10 |
        | 2024 | 76,8% | 8 |
        | *(Mittelfeld, 2008-2026)* | *~55-70%* | *2-13* |
        | 2017 | 42,3% | 8 |
        | 2009 | 43,2% | 2 (sehr dünn) |
        | **2021** | **29,4%** | 9 |

        Trägt über die meisten Jahre der Historie (2009-2026), mit 2021 als
        bisher schwächstem, einigermaßen verlässlichem Jahr. Die meisten
        Einzeljahre haben unter 15 Cluster - **einzelne Jahre sind eher
        Tendenz als Beweis**. Die gepoolte Auswertung über alle ~18 Jahre
        (140+ Cluster, siehe Abschnitt 1) bleibt die robustere Zahl.
        """)

        st.markdown("""
        ##### 9) Trefferwahrscheinlichkeit - zweites, vom Score getrenntes Modell
        Neue Frage, anders als bisher: Nicht "ist das ein Kaufsignal", sondern
        "wie sehr vertraue ich einem *bereits ausgelösten* Signal" (sog.
        Meta-Labeling). Dafür wurden bei allen echten Kaufsignalen (Score ≥ 65,
        3.459 Episoden) systematisch alle verfügbaren Merkmale gegen "eigenes
        Ziel erreicht" (4%/7%, exakt statt genähert) gescreent.

        **So kombiniert man beide in der Praxis** (Dip Score entscheidet ZUERST,
        ob überhaupt ein Signal vorliegt - die Trefferwahrscheinlichkeit ist die
        Priorisierung DANACH, keine Alternative dazu):

        | Situation | Einordnung |
        |---|---|
        | Kein Signal + niedrige/keine Trefferwahrsch. | Normalfall, nichts tun |
        | Signal (🔥/🟡) + hohe Trefferwahrsch. (≥80%) | Stärkster Fall - lief historisch im Schnitt am weitesten |
        | Signal + niedrige Trefferwahrsch. (<65%) | Mehrheitlich immer noch erfolgreich, aber kleinere erwartete Rendite - kein Ignorier-Grund, eher ein Kriterium für die Positionsgröße |
        | Kein Signal, aber 💎 (Trefferwahrsch. ≥75%) | Score reicht noch nicht, aber die zwei stärksten Erfolgsfaktoren stimmen schon - Blick lohnt sich |

        Ergebnis des Screenings:

        | Merkmal | Spanne (Prozentpunkte) |
        |---|---|
        | Sektor | 53,3 (aber: Extremwerte auf 1-2 ETFs zurückzuführen, siehe unten) |
        | EMA50-Score | 20,6 |
        | ATR-Rückgang | 15,3 |
        | GD200-Score/Puffer | 13,6 / 11,3 |
        | Cluster-Größe (gleichzeitige Signale) | 11,3 |
        | RSI-Geschwindigkeit, Volatilitäts-Trend, Sprung-Anteil, Monat | alle < 6 (zu schwach) |

        **Sektor ausführlich geprüft:** Cloud und Cyber Security, früher als
        schwach vermerkt, zeigen mit mehr Daten jetzt überdurchschnittliche Quoten
        (72-75%) - der alte Verdacht war Datenmangel. Konsumgüter fällt zwar mit
        33% deutlich ab, beruht aber nur auf 2 ETFs im gesamten Universum - keine
        verlässliche Sektor-Aussage, eher zwei einzelne schwache Titel.

        **ATR-Rückgang und EMA50-Score wirken nachweislich unabhängig voneinander**
        (in jeder Kombination geprüft, keine Überlagerung) und wurden deshalb zur
        neuen Watchlist-Spalte "Trefferwahrsch." kombiniert:

        | ATR-Rückgang | EMA50-Score | Trefferquote | Ø Rendite (21T) | Cluster (n) |
        |---|---|---|---|---|
        | 0-3 | hoch (15-20) | **91,5%** | **+8,84%** | 129 |
        | 3-5 | hoch (15-20) | 87,7% | +7,09% | 301 |
        | 5-7 | hoch (15-20) | 76,3% | +3,16% | 430 |
        | 5-7 | niedrig (0-10) | 59,4% | +0,09% | 685 |
        | 7+ | niedrig (0-10) | **54,3%** | +0,21% | 164 |

        Trefferquote und tatsächliche Rendite bestätigen sich gegenseitig - kein
        Zufallsfund. Wichtig: Diese Einschätzung fließt bewusst **nicht** in den
        Dip Score selbst ein, sondern steht als eigene, unabhängige Information
        daneben (siehe `trefferwahrscheinlichkeit.py`).

        **Gilt das Muster auch unterhalb der Softsignal-Schwelle?** Geprüft:
        dieselbe Kreuztabelle bei Score 40-65 (12.410 Episoden, quote_5pct statt
        eigenem Ziel) zeigt fast identische Werte (z.B. ATR 0-3/EMA50 hoch:
        90,2% vs. 91,5%) - **kein reiner Extrapolations-Kunstgriff**. Deshalb
        gibt es das 💎-Symbol: Kandidaten ohne Signal, aber mit
        Trefferwahrsch. ≥75%, werden in der Watchlist als "verstecktes Juwel"
        markiert.

        ⚠️ **Einschränkung:** Manche Zellen der Tabelle beruhen auf wenig
        Cluster (z.B. n=87 für die höchste Stufe im letzten Lauf) - eine
        begründete Tendenz, keine Garantie. Das Forward-Tracking-Log zeigt mit
        der Zeit, ob sich das in der Praxis hält.
        """)

        st.caption(
            "⚠️ Diese Zahlen sind ein Stand vom letzten Backtest-Lauf, kein Live-Update - "
            "nach der nächsten Formel-/Schwellen-Anpassung hier manuell nachziehen."
        )
