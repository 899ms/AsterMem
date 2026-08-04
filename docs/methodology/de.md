# So funktioniert AsterMem

Die meisten „KI-Gedächtnis“-Produkte stopfen Ihre Worte in eine Blackbox — Sie erfahren nie, was sich das System gemerkt hat, warum, oder wann es wieder auftaucht. AsterMem geht einen anderen Weg: **Ihr Gedächtnis ist zuerst Ihr Eigentum und erst danach Kontext für die KI.** Dieses Dokument erklärt jede Designentscheidung hinter dem Framework.

## 1. Der Originaltext ist die einzige Wahrheit

Jede Erinnerung wird als reines Markdown gespeichert. Alles, was die KI erzeugt — Zusammenfassungen, Tags, Ihr Profil — ist ein **Derivat**, das sich jederzeit aus der Quelle neu aufbauen lässt.

Das ist kein Purismus. Es schützt vor einem fatalen Degradationspfad: **Paraphrasen von Paraphrasen**. Eine Zusammenfassung ist verlustbehaftete Kompression; fasst das System seine eigenen Zusammenfassungen immer wieder zusammen, entfernt sich jeder Durchlauf weiter von dem, was Sie tatsächlich geschrieben haben — wie die Fotokopie einer Fotokopie, bis die Buchstaben verschwimmen. Deshalb gilt bei AsterMem eine harte Regel: **Jeder KI-Aufruf, der eine Schlussfolgerung erzeugt oder umschreibt, muss den Originaltext als Eingabe erhalten.** Zwischenprodukte dienen nur als Referenz.

Sie können die MD-Dateien mit jedem Editor bearbeiten, der Index synchronisiert sich automatisch. Ihre Daten sind nie in einer Datenbank eingesperrt — der Export ist nichts weiter als das Kopieren eines Ordners.

## 2. Zweistufiger Abruf: Dokumente und Passagen

In einem langen Stück Erinnerungsmaterial sind meist nur ein oder zwei Absätze für die aktuelle Frage relevant. AsterMem zerlegt jede Erinnerung automatisch in **Passagen (Trunks)**, jede mit eigener Zusammenfassung, eigenen Tags und eigenem Embedding. Bei der Abfrage gilt:

- **Stichwortsuche** (Whoosh-Volltextindex) übernimmt exakte Treffer: Namen, Projekte, Fachbegriffe
- **Semantische Suche** (Vektoren) übernimmt unscharfe Absichten: „Worauf wollte ich noch mal achten?“
- **Der Hybridmodus** verschmilzt beide per RRF (Reciprocal Rank Fusion), dynamisch gewichtet nach den Eigenschaften der Anfrage

Die KI erhält passagengenaue Ergebnisse, keine ganzen Dokumente. Kontextfenster sind knapp — 500 treffende Wörter schlagen 5.000 am Thema vorbei.

## 3. Abruf ist Navigation, kein Frage-Antwort-Spiel

Jede Suche liefert mehr als Ergebnisse — sie liefert **Wegweisung für den nächsten Schritt**: IDs semantisch benachbarter Erinnerungen, die nicht angezeigt wurden, Tags aus den Treffern, Dokumente, die sich zu vertiefen lohnen. Die KI muss ihre nächste Anfrage nicht erraten; sie folgt den inneren Verbindungen Ihres Erinnerungsgraphen.

Das ahmt nach, wie Menschen Erinnerungsmaterial zurückverfolgen: Sie bleiben nicht beim ersten Suchtreffer stehen — Sie folgen „der Sache, die diese Quelle erwähnt hat“ immer weiter.

## 4. Profil: „Wer dieser Mensch ist“ in einem einzigen Aufruf

Die KI in jeder Sitzung von Null an lernen zu lassen, wer Sie sind, ist die grundlegende Verschwendung zustandsloser Chats. AsterMems Profilschicht destilliert Ihren gesamten Erinnerungsbestand zu dichtem Kontext, den ein Agent mit einem einzigen `get_profile`-Aufruf abruft.

Das Profil hat drei Quellschichten:

1. **Basisdaten** — strukturierte Felder wie Anrede, Beruf, Zeitzone. Die KI füllt sie automatisch aus Ihren Erinnerungen aus; Sie können alles ändern, und **sobald Sie ein Feld bearbeitet haben, fasst die KI es nie wieder an**. Jede Änderung archiviert den alten Wert im Versionsverlauf.
2. **Ihre eigene Vorstellung** — Markdown, das Sie selbst geschrieben haben und das wortwörtlich an die KI geht. Kein Codepfad im System kann es verändern.
3. **Was die KI weiß** — aus Ihren Erinnerungen destillierte Beobachtungen, gestaffelt in langfristige Eigenschaften, aktuelle Aktivitäten und eine Themenübersicht.

## 5. Jeder von der KI geschriebene Satz ist nachvollziehbar

Jede Schlussfolgerung, die die KI in Ihr Profil schreibt, muss die IDs der Quell-Erinnerungen zitieren. **Nicht belegbare Aussagen werden bereits in der Parsing-Schicht verworfen** — nicht geprüft und gelöscht, sondern gar nicht erst zugelassen.

Erzeugung und Prüfung sind zwei unabhängige KI-Aufrufe: Zuerst werden Kandidaten-Schlussfolgerungen destilliert, dann verifiziert ein Auditor jede einzelne gegen den Originaltext — „stützt die Quelle diese Aussage wirklich?“ Eine tägliche Rückschau rotiert zudem durch die bestehenden Schlussfolgerungen: Gelöschte Quellen werden als „Quelle ungültig“ markiert, lange nicht verifizierte als „möglicherweise veraltet“, und alles landet in einer Liste offener Punkte, über die Sie entscheiden. **Das System löscht nie stillschweigend und glaubt nie stillschweigend.**

## 6. Träumen: seltene, tiefe Konsolidierung

Die tägliche Auswertung sieht nur den Zuwachs des jeweiligen Tages; Muster über Monate hinweg kann sie nicht erkennen. AsterMem greift die von Anthropic-Forschern vorgeschlagene Idee des „Träumens“ (Offline-Konsolidierung) auf: den gesamten Erinnerungsbestand in größeren Abständen neu durchmustern — Duplikate entfernen, Zusammengehöriges verschmelzen, Widersprüche auflösen, langfristige Leitmotive ableiten.

Die entscheidende Designentscheidung: **Tiefe Konsolidierung wird nie direkt wirksam.** Sie erzeugt eine Kandidatenversion; Sie prüfen den Diff (was hinzukam, was verschmolzen, was entfernt wurde) und übernehmen oder verwerfen ihn von Hand. Ausgelöst wird die Konsolidierung ereignisgesteuert — genug neuer Inhalt hat sich angesammelt, offene Punkte häufen sich, ein Massenimport ist abgeschlossen — nicht durch einen starren Cronjob. Menschen machen keinen Großputz nach Kalender; sie räumen auf, wenn es unordentlich aussieht.

Die tiefe Konsolidierung hat auch einen leichtgewichtigen Begleiter im Alltag: das **Aufräumen beim Schreiben**. Sobald eine neue Erinnerung eintrifft, wird sie gegen ähnliche bestehende abgewogen — eine überholte Entscheidung wird abgelöst, ein bereits festgehaltener Fakt nicht doppelt gespeichert. Das Aufräumen archiviert nur, löscht nie; jede Entscheidung landet samt Begründung im Pflegeprotokoll, und alles Archivierte ist mit einem Klick zurückzuholen. Im Zweifel bleibt alles erhalten. Und wer eine Bibliothek ohne Handarbeit bevorzugt, kann Traum-Ergebnisse automatisch wirksam werden lassen — aber nur, wenn jede Schlussfolgerung die Prüfung besteht; alles Fragwürdige wartet weiterhin auf Sie.

## 7. Sichtbar, änderbar, abschaltbar

Ein Profil ist die Zusammenfassung der KI über Sie — womöglich falsch, womöglich einseitig. Deshalb muss das Produkt drei Dinge garantieren:

- **Immer sichtbar** — „was Agenten sehen“ wird wortwörtlich angezeigt; es gibt keine versteckten Prompts
- **Immer änderbar** — jede Schlussfolgerung lässt sich behalten oder löschen, jedes Feld umschreiben
- **Immer abschaltbar** — das Profil ist standardmäßig deaktiviert; ausgeschaltet verursacht es null KI-Aufrufe und keinerlei Kosten

Vertrauen entsteht nicht durch Versprechen. Es entsteht dadurch, dass Sie jederzeit nachsehen und mit einem Klick korrigieren können.

## 8. Für Agenten gebaut

AsterMem ist kein klassisches Dokumentenwerkzeug — es ist ein **Gedächtnis-Backend für Agenten**:

- Eine vollständige Tool-API (Suche, Lesen/Schreiben, Profil) mit Bearer-Token-Authentifizierung und Berechtigungsstufen für Lesen, Schreiben und destruktive Aktionen
- Ein mitgeliefertes Skill-Paket: Cursor, Claude Code und andere Agenten installieren es und legen los
- `quick_match` liefert Zeitkontext, die relevantesten Passagen und Wegweisung für den nächsten Schritt in einem einzigen Aufruf — gebaut für den Sitzungsbeginn
- Mit `capture_conversation` kann ein Agent ein ganzes Gespräch übergeben: Der Text wird wortgetreu gespeichert, und was langfristig erinnernswert ist, wird im Hintergrund in eigenständige Erinnerungen destilliert, jede mit Verweis auf das Original — das Speichern hängt nicht mehr davon ab, dass der Agent ans Speichern denkt
- Suchantworten unterliegen einem Zeichenbudget und einem harten Zeitlimit: Wie groß die Bibliothek auch wird, sie blockiert nie den Zug des Agenten

Sie liefern das Erinnerungsmaterial. Die KI merkt sich, wer Sie sind. Das ist AsterMem.
