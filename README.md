## Was macht er?

- er bekommt einen Url von Youtube und weisst bescheid ob es von einen einzelnen Video ist oder von einen Channel
- er verarbeitet den Script den Videos indem er  einen Database macht mit insights, facts, companies, technologies und searchable transcipt chunks
- er organisiert alle Script auf Channel und Category , perfekt fur Claude Query

## Setup

- install

```c
pip install apify-client anthropic fpdf2
```

- Set API Keys
- run

```c
python app.py
```

## Folder Structure

**Scraper/**

**All/                              <- EVERYTHING combined**

**_All.csv                       <- all videos in one spreadsheet**

**_All_Transcripts.md            <- all transcripts in one file**

**_All_QA.md                     <- Q&A + Analyse**

**_All_Dashboard.md              <-markdown**

**_All_Dashboard.pdf**

**Category/                          <- category you named**

**Channel name/                    <- auto-detected channel**

**Channelname.csv**

**channelname_Transcripts.md**

**channelname_QA.md**

**AI_Explained/**

**AI_Explained.csv**

**AI_Explained_Transcripts.md**

**AI_Explained_QA.md**

**scraper.db                         <- SQLite database (everything)**

## Key Points

- alle files sind nach der Channel gennant Bsp:     **AnastasiInTech.csv**
- _All hat alle videos in einen Ordner
- Categories sind folder die wir nennen sollen wenn wir den Scraper benutzen
- SQlite - Database (scraper.db) kann gesearched werden

## The 6 Tabs

![image.png](attachment:81f151a3-ac6c-460f-bcb0-239e06a70294:image.png)

**Tab 1: Scrape (Herunterladen & Analysieren)**
Das ist das Hauptwerkzeug. Du fügst einen YouTube-Link (einzelnes Video oder ganzer Kanal) ein.

- **Kategorie:** Wähle einen Ordner aus (z. B. „KI_Tools“).
- **Limit:** Bestimme, wie viele Videos maximal geladen werden sollen.
- **Beim Start:** Das System lädt das Video herunter, holt sich den gesprochenen Text, lässt die KI (Claude) alles auf einmal analysieren (Zusammenfassung, Fakten, Themen) und speichert die fertigen Dokumente ab.

![image.png](attachment:95b3e641-a09f-4ab7-b3c5-2861d306e1e8:image.png)

**Tab 2: Bulk Import** 
Hier kannst du hunderte Links auf einmal einfügen oder eine Datei hochladen.

- Du kannst kurze Videos („Shorts“) automatisch herausfiltern und doppelte Links entfernen.
- Alles wird gebündelt auf einmal verarbeitet, was viel Zeit spart.

![image.png](attachment:26e6b357-f357-461f-a8f5-4151ad0653ee:image.png)

**Tab 3: Search & Filter (Suchen & Filtern)**

Durchsuche all deine gespeicherten Videos nach bestimmten Wörtern, Kategorien oder wie gut die Qualität ist. Du siehst sofort alle wichtigen Infos auf einen Blick.

![image.png](attachment:6dd08d8a-679f-43ca-ba04-88d26f8a13db:image.png)

**Tab 4: Channel Comparison (Kanäle vergleichen)**

Vergleiche verschiedene YouTube-Kanäle direkt miteinander. Du siehst, welcher Kanal die meisten Videos hat, wie gut die Qualität ist und welche Themen am häufigsten vorkommen.

![image.png](attachment:c9d69eeb-8a57-4334-82e6-82226340c7bc:image.png)

**Tab 5: AI Insights (KI-Erkenntnisse)**

Die KI schaut sich deine gesamte Datenbank an und liefert dir fertige Auswertungen: Welche Themen gerade im Trend sind, wo es Inhaltslücken gibt und welche Kanäle die besten sind.

![image.png](attachment:baa0e6cf-b451-4479-b03c-099af03edda9:image.png)

**Tab 6: Export (Daten exportieren)**
Du kannst deine gesammelten Daten in vier Formaten herunterladen:

- **JSON:** Für Entwickler und KI-Systeme (enthält absolut alle Details).
- **Markdown:** Ideal, um den Text direkt in einen Chat mit einer KI zu kopieren.
- **Übersicht (KB Summary):** Ein einfaches Inhaltsverzeichnis deiner Datenbank, perfekt als erster Überblick für eine KI.
- **CSV:** Die klassische Tabelle für Excel oder Google Sheets.

## Pipeline

### Wie der Ablauf funktioniert (Einfach erklärt)

**Schritt 1: Daten sammeln (Scraping)**
Das Tool besucht YouTube und sammelt alle wichtigen Infos zum Video: Titel, Kanalname, Datum, Länge, Klicks und vor allem die Untertitel. Beim Massen-Import passiert das für alle Links gleichzeitig.

**Schritt 2: Verlauf prüfen (Doppelte Arbeit vermeiden)**
Das System führt Buch über jedes Video. Hat es ein Video schon einmal erfolgreich bearbeitet, wird es **nie wieder** neu analysiert. Auch Videos, bei denen es einen Fehler gab, werden übersprungen, um Zeit zu sparen.

**Schritt 3: Text extrahieren**
Das Programm sucht auf verschiedenen Wegen nach dem gesprochenen Text (Untertiteln). Das ist wichtig, weil die Daten manchmal unterschiedlich formatiert sind. Wenn gar nichts klappt, nutzt es im Notfall die Videobeschreibung.

**Schritt 4: Text in Häppchen aufteilen (Chunking)**
Lange Texte werden in kleine, sinnvolle Abschnitte zerteilt. Das ist ein schlauer Trick: So kann die KI später gezielt in einem 2-Stunden-Video nach Infos suchen, ohne jedes Mal den kompletten Text lesen zu müssen.

**Schritt 5: KI-Analyse (durch Claude)**
Die KI liest den Text und filtert in nur einem einzigen Durchgang alles Wichtige heraus:

- Eine superkurze Zusammenfassung (3 Sätze)
- Wichtigste Fragen & Antworten
- Eine Bewertung der Qualität (1-10) und des Schwierigkeitsgrads
- Genannte Firmen, Technologien und Finanzdaten
- Die Kernidee und konkrete Fakten

**Schritt 6: Speichern**
Alle gesammelten und analysierten Daten werden ordentlich sortiert und an drei Orten gleichzeitig abgelegt:

1. Im Ordner für den jeweiligen YouTube-Kanal.
2. In einem Hauptordner, in dem alles gesammelt wird.
3. In der Datenbank im Hintergrund.

**Schritt 7: Dashboard (Übersicht)**
Ganz am Ende wird automatisch ein schöner, übersichtlicher Bericht (als Text und PDF) erstellt. Er zeigt dir auf einen Blick, welche Themen, Technologien und Kategorien in deinen Videos am häufigsten vorkommen.

## Datenbank Arhitecture

**1. Videos (Die Haupt-Schublade)**
Hier liegt das Herzstück. Für jedes Video wird ein großer Eintrag mit allen wichtigen Details gespeichert:

- **Grunddaten:** Der Link, der Titel, der Kanal, die Klicks und wie lang das Video ist.
- **KI-Bewertungen:** Zu welcher Kategorie gehört es? Ist es für Anfänger oder Profis? Wie gut ist die Qualität (Note 1-10)?
- **Zusammenfassungen:** Die Kernidee, die Zielgruppe und der wichtigste Ratschlag aus dem Video.
- **Details:** Welche Firmen, Technologien oder Geldsummen wurden erwähnt?
- **Der Text:** Das komplette Transkript und die Fragen & Antworten der KI.

**2. Chunks (Die Text-Häppchen)**
Lange Texte lassen sich langsam durchsuchen. Deshalb wird der komplette Text eines Videos hier in kleine, handliche Stücke (ca. 1500 Zeichen) zerlegt. Jedes Häppchen bekommt eine Nummer, damit die KI später blitzschnell genau die richtige Stelle in einem stundenlangen Video findet.

**3. Facts (Fakten & Zahlen)**
Hier sammelt das System ganz gezielt handfeste Informationen heraus:

- Was genau wurde gesagt? (Eine Behauptung, eine Statistik oder ein Zitat).
- Wie lautet die genaue Zahl oder Aussage?
- Wie sicher ist sich die KI, dass diese Information verlässlich ist (hoch, mittel, niedrig)?

**4. Entity Index (Das Namensregister)**
Ein schlaues Inhaltsverzeichnis für alles Wichtige.

- Hier wird jede erwähnte Firma, Person oder Software (z. B. „OpenAI“ oder „LangChain“) aufgelistet.
- Das System merkt sich genau, in welchem Video der Name vorkam und *in welchem Zusammenhang* darüber gesprochen wurde.

**5. History (Der Verlauf)**
Das ist das Gedächtnis des Programms, um nie etwas doppelt zu machen.

- Es merkt sich jedes Video, das jemals angefasst wurde.
- Es speichert den Status: Hat es geklappt („done“) oder gab es einen Fehler („failed“)?
- Es notiert sich, wann und wie oft versucht wurde, das Video herunterzuladen.

**6. Channels (Kanal-Statistiken)**
Hier geht es nicht um einzelne Videos, sondern um die YouTuber selbst. Das System rechnet alles zusammen:

- Wie viele Videos wurden von diesem speziellen Kanal schon analysiert?
- Welche Durchschnittsnote haben die Videos?
- Was ist das absolute Lieblingsthema und über welche Software spricht dieser Kanal am meisten?

## Integration with Claude

### Wie man es mit Claude nutzt (Das ist der eigentliche Sinn des Ganzen)

**Option A: Schneller Chat (Markdown einfügen)**

- Gehe zum Tab „Export“ > **Claude Markdown**
- Öffne die exportierte .md-Datei
- Füge sie in einen Chat mit Claude ein
- Stelle Fragen: *„Welche Tools werden am meisten erwähnt?“*, *„Vergleiche die Kanäle X und Y“*, *„Was hat Video Z über Preise gesagt?“*

**Option B: Strukturierte Abfragen (JSON über API)**

- Gehe zum Tab „Export“ > **JSON Knowledge Base**
- Nutze die exportierte .json-Datei in deinen API-Aufrufen für Claude
- Das JSON enthält einen **entity_index** (Namensregister) – Claude kann jede Firma, Person oder jedes Tool nachschlagen und sofort finden, in welchen Videos sie erwähnt werden
- Das **chunks**Array (Text-Häppchen) ermöglicht es Claude, bestimmte Teile der Transkripte gezielt zu durchsuchen
- Das **key_facts**Array liefert Claude spezifische Behauptungen inklusive deren Verlässlichkeit (Confidence Levels)

**Option C: Mit der Zusammenfassung starten**

- Exportiere zuerst die **KB Summary** (Datenbank-Übersicht)
- Gib diese an Claude: *„Hier ist ein Überblick, was in meiner Datenbank steht.“*
- Gib ihm erst danach das komplette JSON oder Markdown
- Claude kennt nun die Struktur deiner Daten, bevor er tief in die Details eintaucht

---

### Die besten Prompts (Fragen) für Claude:

- **"Based on this knowledge base, what are the top 3 trends in AI tooling?"**
- **"Which videos mention [Company X]? What do they say?"**
- **"Find all statistics about revenue/pricing from these videos"**
- **"Compare what Channel A says about [topic] vs Channel B"**
- **"What are the most controversial claims across all videos?"**
- **"Generate a briefing document on [topic] using only facts from this database"**

**Cost breakdown**

| **Component** | **Cost per video** | **100 videos** | **300 videos** |
| --- | --- | --- | --- |
| **Apify scraping** | **~$0.005** | **~$0.50** | **~$1.50** |
| **Claude Haiku (1 call)** | **~$0.003** | **~$0.30** | **~$0.90** |
| **Total** | **~$0.008** | **~$0.80** | **~$2.40** |

**Insights tab: ~$0.003 per generation (one-time, not per video). Shorts with no transcript still cost the Apify scrape but skip the Claude call.**
