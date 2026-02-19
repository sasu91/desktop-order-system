================================================================
 DesktopOrderSystem — Portable Edition
================================================================

AVVIO
-----
1. Estrarre la cartella DesktopOrderSystem/ in qualsiasi
   posizione (C:\Apps\, Desktop, chiavetta USB, ecc.).
2. Doppio click su  DesktopOrderSystem.exe  per avviare.
   Nessuna installazione richiesta.  Nessun Python necessario.

STRUTTURA CARTELLE
------------------
DesktopOrderSystem\
  DesktopOrderSystem.exe    <- eseguibile principale
  _internal\                <- librerie (non modificare)
  data\                     <- database + CSV + impostazioni
  logs\                     <- log rotanti (max 15 MB)
  README.txt                <- questo file

DATI E DATABASE
---------------
* Tutti i dati sono nella sottocartella  data\
  (o in %APPDATA%\DesktopOrderSystem\data\ se la cartella
  dell'EXE non è scrivibile).
* data\app.db   = database principale SQLite
* data\settings.json = impostazioni / configurazione

BACKUP
------
* Backup automatici prima delle migrazioni del DB:
    data\backups\
* CSV di emergenza:
    data\csv_backups\

AGGIORNAMENTO
-------------
Per aggiornare: sostituire i file nella cartella _internal\
con quelli della nuova versione.  La cartella data\ NON va
toccata (conserva tutti i dati).

SUPPORTO
--------
Log degli errori: logs\desktop_order_system_YYYYMMDD.log
In caso di problema, allegare il log alla segnalazione.

================================================================
