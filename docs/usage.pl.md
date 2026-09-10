# StepAgent Terminal — obsługa

## Widoki i sterowanie

Oś kroków grupuje aktywności numerami tur. Każdy krok zajmuje dwa wiersze: pierwszy podaje turę, numer kroku, rodzinę / typ i stan, a drugi — odmianę działania oraz czas, jeśli mieści się w panelu. Przykład: `WYKONANIE / PROCES` i `Uruchomienie polecenia`. Treść komendy i wynik są w szczegółach. Odmiana wynika z akcji i metadanych; nieznany podtyp pozostaje widoczny pod nazwą źródłową, a akcje wnioskowane mają oznaczenie `≈`. Wiadomości rozróżniają rolę i zapisany kanał, w tym aktualizację i odpowiedź końcową. Wąski panel najpierw pomija godzinę i dodatkowy czas, następnie rodzinę, zachowując typ i stan; długie etykiety mają wielokropek. Filtr `/` przeszukuje również te etykiety, zachowując wyszukiwanie po treści poleceń.

Aktywny wybór ma znacznik `▶` oraz stałe jasne tło z czarnym tekstem na obu wierszach, niezależnie od kategorii i domyślnego tła terminala. Po przejściu do szczegółów wybrany krok zachowuje `│` przy obu wierszach i pogrubiony tytuł. Bez kolorów działa odwrócenie obu wierszy oraz te same znaczniki. Nagłówek `KROKI · ↑↓ wybór` wskazuje fokus; pulsujący pasek przy krawędzi panelu jest dodatkową wskazówką (cykl 1,5 s, zmiana grubości znaku, bez migotania). Stany rozróżniają m.in. oczekiwanie, blokadę, błąd, anulowanie i przerwanie. `ZAKOŃCZONY` opisuje zakończenie kroku, nie potwierdza sukcesu operacji.

W szerokim terminalu (od 110 kolumn) obok znajduje się panel treści, wyników i relacji; w mniejszym oknie szczegóły otwierają się osobno. Minimalny obszar to 42×12, a wygodny rozmiar to 120×35 lub większy. Zmiana rozmiaru nie przerywa odczytu.

Po naciśnięciu `/` kontrastowy pasek `WYSZUKIWANIE` przejmuje fokus, a podpowiedzi na dole pokazują `Esc: wyjdź i usuń filtr` oraz `Enter: nawigacja z filtrem`. `Esc` usuwa wpisane zapytanie i wraca do nawigacji; `Enter` zachowuje zapytanie, oznaczone następnie jako `FILTR AKTYWNY`. Długie zapytanie pokazuje końcówkę i kursor także przy szerokości 42 kolumn. Wyszukiwanie kroków zatrzymuje podążanie LIVE i przenosi fokus ze szczegółów na listę kroków; odczyt sesji w tle nadal działa. Te same oznaczenia i skróty obowiązują w wyszukiwarce sesji.

| Klawisz | Działanie |
| --- | --- |
| `s` | Wybór sesji |
| `↑` / `↓`, `k` / `j` | Wybór kroku; przewijanie po przeniesieniu fokusu do szczegółów |
| `←` / `→` | Fokus: oś / szczegóły |
| `d` | Powiększ Treść wybranego kroku na cały panel / zmniejsz do osi |
| `Enter` | Otwórz / zamknij pełny panel; w warstwie relacji otwiera cel |
| `1`, `2`, `3`, `4` | Treść / wszystkie pola / relacje / źródła RAW |
| `a`, `r` | Wszystkie warstwy razem / przełącz RAW |
| `n`, `N` | Następna / poprzednia relacja w warstwie 3 |
| `b` | Powrót z relacji do poprzedniego kroku, warstwy i pozycji |
| `u` | Wczytaj nowe dane sesji do czytanych szczegółów bez resetowania pozycji |
| `Spacja`, `f` | Podążanie za nowymi krokami / przeglądanie historii |
| `g` / `G` | Pierwszy krok / najnowszy krok z włączeniem śledzenia |
| `PgUp` / `PgDn` | Przewiń stronę |
| `[` / `]` | Poprzednia / następna tura |
| `t` | Tylko wybrana tura / wszystkie tury |
| `/` | Filtr kroków; Enter zatwierdza |
| `Tab`, `m` | Macierz aktywności / oś kroków |
| `←` / `→` w macierzy | Wybór tury; Enter otwiera jej kroki |
| `↑` / `↓` w macierzy | Przewiń kategorie, jeśli nie mieszczą się na ekranie |
| `Esc` | Powrót i wyczyszczenie filtrów |
| `?` | Pomoc |
| `q`, `Ctrl+C` | Wyjście i przywrócenie terminalu |

Macierz liczy kroki według kategorii i tur, zgodnie z aktualnymi filtrami. Nowe rekordy nadal są odczytywane w trybie historii, podczas wyszukiwania i w selektorze sesji. Zmieniona istniejąca interakcja (np. nadejście wyniku komendy) aktualizuje ten sam krok.

Wszystkie znormalizowane kroki od pierwszej wiadomości użytkownika są widoczne, również tokeny, kontekst, zdarzenia cyklu życia i nieznane typy. Pomijana jest wyłącznie inicjalizacja przed pierwszym promptem. Skrót `p` nie ukrywa już kategorii. Własne filtry tekstu i tury można wyczyścić przez `Esc`. Licznik kroków odnosi się do części sesji po pierwszym prompcie; liczba wpisów inicjalizacyjnych jest podana osobno. Wywołanie i jego wynik nadal stanowią jeden krok ze wszystkimi rekordami źródłowymi.

Panel szczegółów ma osobne prezentacje typów: wiadomości i analiza pokazują tekst, komendy — polecenie, parametry, stdout/stderr i kod wyjścia, poprawki — diff z nagłówkiem pliku, operacją i bilansem `+N −M`, numerowanymi liniami i znacznikiem `+`/`-` powtarzanym w zawiniętych wierszach (zielone dodania, czerwone usunięcia), plany — zadania ze statusami, pytania — treść i opcje, a tokeny — liczniki z rozróżnieniem sesji, tury i wywołania. MCP, web, agenci, multimedia i zdarzenia protokołu wyróżniają właściwe im pola. Nagłówki sekcji mają jeden styl — `── NAZWA ───` z linią do krawędzi panelu — niezależny od typu kroku; kolor typu zostaje w nagłówku szczegółów, a status niosą tylko sekcje wyniku i błędu. Strumienie wyniku (OUTPUT, STDERR) są wciętymi podsekcjami. JSON i kod mają kolorowanie składni; kolor i pogrubienie są zachowane przy zawijaniu. Relacje jawne, wnioskowane i kolejność zapisu są rozróżniane kolorem oraz etykietą. Numery linii diffa pochodzą ze źródła, gdy patch je zapisuje (unified diff, nowy plik); w kopercie `apply_patch` z gołym `@@` źródło nie podaje pozycji w pliku, więc widoczna jest numeracja kolejna w patchu, opisana etykietą przy nagłówku pliku.

### Warstwowe szczegóły — wdrożony układ

Stały nagłówek zawiera numer kroku, rodzinę, typ, podtyp, akcję, stan, czas i zapisany kod wyjścia. Przy bardzo niskim oknie jest kompaktowy. Domyślne tło i kolor tekstu pochodzą z terminalu — profil Homebrew pozostaje czarno-zielony, z akcentami ANSI.

Podpowiedzi `d powiększ` / `d zmniejsz` i `r RAW` są stale widoczne na dole, również w wąskim terminalu. `d` powiększa **Treść aktualnie wybranego kroku**, a `r` przełącza jego RAW i Treść bez zmiany kroku ani zamykania powiększenia.

Wrapper `exec` ma osobny układ także wtedy, gdy parser klasyfikuje go jako komendę: odczytane literalne argumenty, wielowierszowe polecenie, parametry, rozpakowane odpowiedzi i pełny oryginalny skrypt. Zagnieżdżony JSON oraz bloki `input_text`/`output_text` w wyniku pokazują czytelny tekst; kod wyjścia i metadane pozostają osobno. Statyczny odczyt argumentów niczego nie wykonuje i nie dowodzi wykonania kodu (np. warunkowej gałęzi). Argumenty dynamiczne pozostają w skrypcie bez zgadywania. Nie zamieniamy dosłownych `\\n` w dowolnym tekście ani ścieżkach — dekodowane są tylko poprawne struktury JSON i rozpoznane literały. RAW i wszystkie wersje źródeł nie są modyfikowane.

- `1 Treść`: osobne układy dla wszystkich 35 typów modelu oraz wariantów akcji; m.in. indeks plików i diff, sterowanie procesem z escaped wejściem, pełny skrypt wrappera, role wiadomości, bloki MCP i multimedia, osobne zakresy tokenów. Lista faz wskazuje rekordy źródłowe.
- `2 Wszystkie pola`: każda wersja źródłowa osobno, ze ścieżkami JSON Pointer i typami wartości. `false`, `0`, `null`, `""`, `[]` i `{}` są zachowane. Normalizacja znajduje się w oddzielnej sekcji; nie zastępuje wcześniejszych wartości.
- `3 Relacje`: kierunek, cel, typ, pewność, jawność/wnioskowanie i komplet metadanych. `n`/`N` wybiera relację, Enter otwiera cel, `b` lub Esc przywraca poprzednią kartę. Można przejść do sesji, agenta, tury, zadania planu, artefaktu czy błędu grafowego — bez dodawania tych węzłów do osi kroków. Historia nawigacji obejmuje ostatnie 32 przejścia.
- `4 Źródła RAW`: kompletne wartości wszystkich przypisanych rekordów JSON, w kolejności źródłowej. To sformatowany JSON po parsowaniu, nie kopia bajtowa oryginalnych linii (np. odstępy mogą być inne). Znaki sterujące są escaped, nie wykonywane przez terminal.

`a` pokazuje wszystkie warstwy kolejno, z pełnymi metadanymi każdej relacji. Pozycja jest pamiętana osobno dla kroku i warstwy. Licznik `30+` oznacza, że dalsze wiersze zostaną wyrenderowane podczas przewijania — nie obcięcie danych. Przejście do szczegółów zatrzymuje podążanie i przypina migawkę. Gdy nadejdzie nowy zapis sesji, pojawia się `NOWE DANE SESJI: u`; `u` odświeża czytaną kartę, a `G` wraca do najnowszego kroku i śledzenia LIVE. Odczyt w tle nigdy nie zatrzymuje się z powodu czytania.

## Co oznacza „real time” i związek przyczynowy

Odczyt następuje domyślnie co 250 ms. Monitor czyta tylko dopisane bajty; nie importuje ponownie całego pliku. Praca z plikiem i budowa grafu odbywają się w osobnym wątku, a interfejs nadal reaguje na klawiaturę. Faktyczna latencja obejmuje czas zapisu po stronie Codexa oraz parsowania. To obserwacja lokalnego logu, nie strumień tokenów z modelu: zdarzeń niezapisanych przez Codexa nie da się tu wyświetlić.

`NOWY ZAPIS` oznacza zmianę pliku w ostatnich 120 sekundach, nie dowód, że proces nadal pracuje. Stan tury wynika z ostatnich zapisanych zdarzeń startu/zakończenia; po awarii Codexa stara tura może pozostać otwarta w logu. `LIVE` oznacza włączone podążanie widoku. Czasy kroków są wyświetlane w lokalnej strefie terminalu.

Wywołanie i wynik łączy wspólny `call_id` lub identyfikator elementu. Rdzeń v4 zachowuje kategorie aktywności, powiązania z plikami, relacje agentów, ponowienia i metadane pewności. Panel szczegółów odróżnia relacje jawne od wnioskowanych. Strzałka na osi i relacja `next` oznaczają kolejność zapisu, która sama nie dowodzi przyczynowości. Podsumowanie analizy jest dostępne tylko wtedy, gdy zapisano je w źródle.

Niepełna linia JSONL, również z podzielonym znakiem UTF-8, czeka na końcowy znak nowej linii. Niepoprawny JSON lub rekord niebędący obiektem jest pomijany z widocznym licznikiem. Poprawne duże rekordy nie mają dawnego limitu 16 MiB. Nieznane poprawne rekordy zachowują pełną treść. Przy obcięciu pliku lub zmianie jego inode stan zostaje odbudowany; brak pliku powoduje oczekiwanie na jego powrót. Podmiana treści w miejscu bez zmiany rozmiaru/inode nie jest wykrywana — wtedy wybierz sesję ponownie. Panel renderuje dane przyrostowo, bez dawnego limitu 200 tys. znaków. Historia sesji i odwiedzone strony nadal zajmują RAM: nie jest to dyskowa baza o stałym zużyciu pamięci. Wielkie logi wpływają na pamięć, parsowanie i budowanie grafu. Zmiana szerokości ponownie zawija tekst; zachowany numer wiersza może odpowiadać innemu fragmentowi tekstu.

Odczyt sesji jest wyłącznie lokalny i tylko do odczytu. Program nie uruchamia poleceń znalezionych w logach, nie zapisuje do katalogu Codexa i nie wysyła danych do sieci. Terminal pokazuje treść rozmowy i argumenty narzędzi — uwzględnij to przy udostępnianiu ekranu.

Kontekst integracji: [oficjalna dokumentacja Codex App Server](https://learn.chatgpt.com/docs/app-server). Implementacja korzysta z istniejącego parsera projektu i lokalnie sprawdzonych rolloutów; nie uruchamia ani nie przejmuje sesji przez App Server.
