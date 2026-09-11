# StepAgent Terminal

Terminalowy podgląd lokalnych sesji Codexa: prompty, aktywność agenta,
wywołania narzędzi i ich wyniki, poprawki plików oraz odpowiedzi końcowe.

Działa na macOS, Linuxie i Windowsie z Pythonem 3.10+.

## Instalacja

Sklonuj repozytorium i uruchom polecenia w jego katalogu.

### macOS / Linux

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/stepagent-terminal
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\stepagent-terminal.exe
```

Na Windowsie instalacja dołącza `windows-curses`. Potrzebny jest terminal
obsługujący Unicode. Aktywowanie środowiska wirtualnego nie jest wymagane.

Możesz też użyć launcherów z katalogu repozytorium: `sh run.sh --latest`,
`python run.py --latest` lub `.\run.ps1 --latest`.

## Wybór logów sesji

Program nie zawiera ścieżki zależnej od konkretnego komputera. Katalog jest
wybierany w tej kolejności:

1. `--sessions-dir DIRECTORY`
2. zmienna `CODEX_SESSIONS_DIR`
3. `CODEX_HOME/sessions`
4. `.codex/sessions` w katalogu domowym bieżącego użytkownika

Obsługiwane są `~`, zmienne środowiskowe, ścieżki względne, spacje i Unicode.
Program skanuje zagnieżdżone pliki `*.jsonl` i niczego w nich nie modyfikuje.

```sh
stepagent-terminal --latest
stepagent-terminal --list
stepagent-terminal --session ID_LUB_FRAGMENT
stepagent-terminal --file "path/to/rollout.jsonl"
stepagent-terminal --snapshot --file "path/to/rollout.jsonl"
stepagent-terminal --sessions-dir "path/to/sessions"
```

`--latest` wybiera sesję raz. `--snapshot` wypisuje jednorazowy JSON i działa
bez interaktywnego terminala. Domyślny interwał odczytu to 0,25 s; można go
zmienić przez `--interval` (minimum 0,05 s).

Przykładowa konfiguracja:

```sh
export CODEX_SESSIONS_DIR="$HOME/my-session-logs"
```

```powershell
$env:CODEX_SESSIONS_DIR = Join-Path $HOME 'my-session-logs'
```

## Sterowanie

Wygodny rozmiar okna to co najmniej 120 × 35 znaków; minimum to 42 × 12.

| Klawisz | Działanie |
| --- | --- |
| `s` | wybór sesji |
| `↑` / `↓`, `j` / `k` | wybór kroku lub przewijanie szczegółów |
| `←` / `→` | fokus osi / szczegółów |
| `d`, `Enter` | szczegóły pełnoekranowe |
| `1` / `2` / `3` / `4` | treść / pola / relacje / RAW |
| `a`, `r` | wszystkie warstwy / przełącz RAW |
| `u` | odśwież przypięte szczegóły |
| `Space`, `f` | podążaj za nowymi krokami |
| `g` / `G` | pierwszy / najnowszy krok |
| `[` / `]`, `t` | poprzednia / następna tura; filtr tury |
| `/` | wyszukiwanie; `Enter` zachowuje filtr |
| `Tab`, `m` | oś kroków / macierz aktywności |
| `Esc` | powrót lub wyczyszczenie filtra |
| `?` | pomoc |
| `q`, `Ctrl+C` | wyjście |

Pełny opis znajduje się w [polskiej instrukcji](docs/usage.pl.md).

## Demo

Demo tworzy syntetyczny log w katalogu tymczasowym systemu:

```sh
stepagent-demo --delay 1 --turns 3
```

Następnie uruchom polecenie wyświetlone przez demo w drugim terminalu.
Demo nie czyta ani nie zmienia prawdziwych sesji.

## Prywatność i ograniczenia

Program działa lokalnie, tylko do odczytu. Nie wysyła danych do sieci, nie
wykonuje poleceń znalezionych w logach i nie wymaga klucza API. Logi mogą
zawierać prywatne prompty oraz argumenty narzędzi — przed udostępnieniem ekranu
lub pliku usuń wrażliwe treści.

To obserwator zapisanego pliku JSONL, a nie strumień tokenów modelu. Niepełne
wiersze czekają na znak nowej linii; uszkodzone są pomijane z licznikiem.
Obcięcie lub podmiana pliku odbudowuje stan. Duże logi zwiększają zużycie pamięci.

## Sprawdzanie

```sh
python -m unittest discover -s tests -v
python -m build
```

GitHub Actions uruchamia testy i budowanie paczki na Windowsie, macOS i Linuxie
z Pythonem 3.10 oraz 3.14.

## Licencja

MIT — szczegóły w [LICENSE](LICENSE).
