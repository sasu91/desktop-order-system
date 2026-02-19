# CHECKLIST_BUILD.md — Test su macchina pulita & procedura build

## Procedura build (1 comando)

```bat
:: Su macchina Windows con Python 3.12 x64 sul PATH
git clone <repo> DesktopOrderSystem-src
cd DesktopOrderSystem-src
build.bat
```
Output: `dist\DesktopOrderSystem\DesktopOrderSystem.exe`

---

## Checklist test su macchina "pulita" (senza Python installato)

| # | Test | Atteso | Esito |
|---|------|--------|-------|
| 1 | Copia `dist\DesktopOrderSystem\` in una VM Windows 10/11 SENZA Python | — | — |
| 2 | Doppio click su `DesktopOrderSystem.exe` | Finestra GUI si apre senza errori | ☐ |
| 3 | Verificare che `data\` venga creata accanto all'EXE al primo avvio | Cartella `data\app.db` presente | ☐ |
| 4 | Verificare che `logs\` venga creata accanto all'EXE | File `.log` presente in `logs\` | ☐ |
| 5 | Aprire tutte le tab (Stock, Proposta Ordini, Conferma, Ricevimento, Eccezioni, Dashboard) | Nessun traceback/crash | ☐ |
| 6 | Aggiungere un nuovo SKU via GUI | SKU salvato nel DB | ☐ |
| 7 | Generare una proposta d'ordine | Proposta visibile nella tabella | ☐ |
| 8 | Registrare un'eccezione WASTE | Evento nel ledger, stock aggiornato | ☐ |
| 9 | Export CSV da menu File → Esporta | File CSV creato nella posizione scelta | ☐ |
| 10 | Chiudere e riaprire l'EXE | Dati precedenti ancora presenti | ☐ |
| 11 | Verificare che `data\` e `logs\` NON siano in `%TEMP%` né in `sys._MEIPASS` | Posizione accanto all'EXE | ☐ |

---

## Issue note e risolte

### matplotlib TkAgg backend
- **Sintomo**: `ImportError: cannot import name 'FigureCanvasTkAgg'`
- **Fix**: aggiunto `matplotlib.backends.backend_tkagg` e `matplotlib.backends._backend_tk` in `hiddenimports` dello spec.

### tkcalendar / babel
- **Sintomo**: `ModuleNotFoundError: No module named 'babel.numbers'`
- **Fix**: aggiunto `babel`, `babel.numbers`, `babel.dates`, `babel.core` in `hiddenimports`.

### python-barcode writer plugins
- **Sintomo**: `KeyError: 'SVG'` o writer non trovato
- **Fix**: `collect_submodules("barcode")` nel spec raccoglie tutti i plugin dinamici.

### migrations/ SQL non trovate (primo avvio, init DB)
- **Sintomo**: app parte ma DB non viene inizializzato (tabelle mancanti)
- **Fix**: `migrations/` directory aggiunta a `datas` nello spec; `paths.get_migrations_dir()` ritorna `sys._MEIPASS/migrations` quando frozen.

### paths relative a cwd
- **Sintomo**: `data/` e `logs/` creati nella working directory dell'utente, non accanto all'EXE
- **Fix**: `src/utils/paths.py` — tutte le directory runtime derivate da `sys.executable` quando frozen.

---

## Struttura distribuzione ZIP finale

```
DesktopOrderSystem.zip
└── DesktopOrderSystem\
    ├── DesktopOrderSystem.exe      ← avviare questo
    ├── _internal\                  ← librerie (non toccare)
    ├── data\                       ← dati (creata al primo avvio)
    ├── logs\                       ← log (creata al primo avvio)
    └── README.txt                  ← istruzioni utente
```

---

## Note antivirus / distribuzione

- Build **onedir** (non onefile): riduce i falsi positivi antivirus perché i file DLL non vengono estratti in `%TEMP%` a ogni avvio.
- Per firma digitale: aggiungere `signtool.exe` a build.bat dopo la build (richiede certificato code-signing).
- UPX abilitato nello spec per ridurre le dimensioni; disabilitarlo (`upx=False`) se l'antivirus blocca su UPX-packed DLL.
