# API Contract

Specifica degli endpoint previsti per il backend FastAPI di **desktop-order-system**.

> **Stato aggiornato — Febbraio 2026**: `GET /health` ✅ · `GET /skus/by-ean/{ean}` ✅ · `GET /stock/{sku}` ✅ · `GET /stock` ✅ · `POST /exceptions` ✅ · `POST /receipts/close` ✅  
> Il backend risiede in `backend/dos_backend/` e condivide il database SQLite con il client desktop.

---

## Indice

1. [Convenzioni generali](#1-convenzioni-generali)
2. [Autenticazione](#2-autenticazione)
3. [Formato errori](#3-formato-errori)
4. [Endpoints](#4-endpoints)
   - [GET /health](#get-health)
   - [GET /skus/by-ean/{ean}](#get-skusby-eanean)
   - [GET /stock](#get-stock)
   - [GET /stock/{sku}](#get-stocksku)
   - [POST /exceptions](#post-exceptions)
   - [POST /receipts/close](#post-receiptsclose)
5. [Regole di idempotenza](#5-regole-di-idempotenza)
6. [Validazione e vincoli](#6-validazione-e-vincoli)

---

## 1. Convenzioni generali

| Proprietà | Valore |
|---|---|
| Base URL (sviluppo) | `http://127.0.0.1:8000/api/v1` |
| Formato corpo | `application/json` (UTF-8) |
| Date | ISO 8601 `YYYY-MM-DD` (stringa) |
| Quantità | Intero ≥ 0 nel payload (il segno viene assegnato internamente in base all'event type) |
| Paginazione | Query param `?page=1&page_size=50` dove rilevante |
| Versioning | Prefisso `/api/v1`; breaking changes → nuovo prefisso `/api/v2` |

**Header richiesti su ogni chiamata autenticata:**

```
Authorization: Bearer <DOS_API_TOKEN>
Content-Type: application/json
```

---

## 2. Autenticazione

Bearer token statico configurato via `DOS_API_TOKEN` (vedi [docs/config.md](config.md)).

- Token assente o errato → `401 Unauthorized`
- Endpoint `/health` è **pubblico** (no token richiesto)

---

## 3. Formato errori

Tutti gli errori seguono questa struttura unificata:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Descrizione leggibile del problema",
    "details": [
      {
        "field": "qty",
        "issue": "Deve essere un intero >= 1"
      }
    ]
  }
}
```

### Codici HTTP standard

| HTTP | `error.code` | Quando |
|---|---|---|
| `400` | `VALIDATION_ERROR` | Campo mancante, tipo errato, valore fuori range |
| `401` | `UNAUTHORIZED` | Token assente o non valido |
| `404` | `NOT_FOUND` | SKU, receipts o risorsa inesistente |
| `409` | `CONFLICT` | Risorsa già esistente (violazione idempotency key) |
| `422` | `BUSINESS_RULE_ERROR` | Vincolo di dominio violato (es. EAN non valido) |
| `500` | `INTERNAL_ERROR` | Errore non gestito lato server |

---

## 4. Endpoints

---

### GET /health

Verifica che il servizio sia attivo e raggiungibile.

**Autenticazione**: nessuna

#### Response `200 OK`

```json
{
  "status": "ok",
  "version": "0.1.0",
  "db_path": "/data/app.db",
  "db_reachable": true,
  "storage_backend": "sqlite",
  "dev_mode": false,
  "timestamp": "2026-02-24T10:30:00Z"
}
```

> `storage_backend`: `"sqlite"` | `"csv"` — backend attivo.  
> `dev_mode`: `true` quando `DOS_API_TOKEN` non è configurato (bypassa autenticazione con WARNING).

#### Response `200 OK` (DB non raggiungibile — stato degradato)

```json
{
  "status": "degraded",
  "version": "0.1.0",
  "db_path": "/data/app.db",
  "db_reachable": false,
  "storage_backend": "sqlite",
  "dev_mode": false,
  "timestamp": "2026-02-24T10:30:00Z"
}
```

> **Nota**: `/health` non restituisce mai `503`. Lo stato del DB è espresso nel payload (campo `db_reachable`).

---

### GET /skus/by-ean/{ean}

Cerca un SKU tramite codice EAN. Utile per il client Android (scansione barcode).

**Autenticazione**: richiesta (Bearer token)

**Parametri path**:

| Param | Tipo | Note |
|---|---|---|
| `ean` | `string` | Codice EAN-12 o EAN-13 (solo cifre). EAN-8 non supportato. |

#### Response `200 OK`

```json
{
  "sku": "PRD-0042",
  "description": "Latte intero UHT 1L",
  "ean": "8001234567890",
  "ean_valid": true,
  "moq": 6,
  "pack_size": 6,
  "lead_time_days": 3,
  "safety_stock": 12,
  "shelf_life_days": 90,
  "in_assortment": true,
  "category": "DAIRY",
  "department": "FRESH"
}
```

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Nessun SKU trovato per EAN '8001234567890'",
    "details": []
  }
}
```

#### Response `400 Bad Request` (EAN non valido)

```json
{
  "error": {
    "code": "BAD_REQUEST",
    "message": "EAN non valido: attesi 12 o 13 digit, got 8 digits",
    "details": []
  }
}
```

> **Nota**: EAN malformato già presente nel DB (legacy) viene restituito con `ean_valid: false` e warning loggato — **mai crash**.

---

### GET /stock

Lista stock calcolato AsOf per tutti gli SKU (o un sottoinsieme filtrato).

**Query params**:

| Param | Tipo | Default | Note |
|---|---|---|---|
| `asof` | `string` (date) | oggi | Data di calcolo stock (es. `2026-02-24`) |
| `sku` | `string` (ripetibile) | tutti | Filtra su uno o più SKU: `?sku=PRD-001&sku=PRD-002` |
| `in_assortment` | `boolean` | `true` | `false` per includere SKU dismessi |
| `page` | `integer` | `1` | Pagina (1-based) |
| `page_size` | `integer` | `50` | Righe per pagina (max 200) |

#### Response `200 OK`

```json
{
  "asof": "2026-02-24",
  "page": 1,
  "page_size": 50,
  "total": 2,
  "items": [
    {
      "sku": "PRD-0042",
      "description": "Latte intero UHT 1L",
      "on_hand": 48,
      "on_order": 24,
      "last_event_date": "2026-02-22"
    },
    {
      "sku": "PRD-0055",
      "description": "Yogurt bianco 125g",
      "on_hand": 0,
      "on_order": 0,
      "last_event_date": null
    }
  ]
}
```

---

### GET /stock/{sku}

Stock calcolato ledger-AsOf per un singolo SKU, con dettaglio degli ultimi eventi.

**Autenticazione**: richiesta (Bearer token)

**Parametri path**: `sku` — codice SKU esatto (case-sensitive)

**Query params**:

| Param | Tipo | Default | Note |
|---|---|---|---|
| `asof_date` | `string` (YYYY-MM-DD) | oggi | Data di riferimento per il calcolo |
| `mode` | `POINT_IN_TIME` \| `END_OF_DAY` | `POINT_IN_TIME` | Semantica della data (vedi sotto) |
| `recent_n` | `integer` | `20` | Numero di transazioni recenti restituite (0–200) |

**Semantica `mode`**

| `mode` | Condizione interna al calcolo | Significato pratico |
|--------|-------------------------------|---------------------|
| `POINT_IN_TIME` | `date < asof_date` | Stock **all'apertura** di `asof_date`; gli eventi del giorno stesso sono esclusi |
| `END_OF_DAY` | `date < asof_date + 1d` | Stock **alla chiusura** di `asof_date`; gli eventi del giorno stesso sono inclusi |

La trasformazione avviene nel router: il dominio riceve sempre `effective_asof` con semantica `date < effective_asof`.

#### Esempio 1: stock all'apertura del 25 febbraio (mode=POINT_IN_TIME)

```
GET /stock/PRD-0042?asof_date=2026-02-25&mode=POINT_IN_TIME
```

```json
{
  "sku": "PRD-0042",
  "description": "Latte intero UHT 1L",
  "asof": "2026-02-25",
  "mode": "POINT_IN_TIME",
  "on_hand": 48,
  "on_order": 24,
  "unfulfilled_qty": 0,
  "last_event_date": "2026-02-24",
  "recent_transactions": [
    {
      "transaction_id": null,
      "date": "2026-02-24",
      "event": "RECEIPT",
      "qty": 24,
      "receipt_date": "2026-02-24",
      "note": "OC-20260220-001"
    },
    {
      "transaction_id": null,
      "date": "2026-02-24",
      "event": "SALE",
      "qty": 12,
      "receipt_date": null,
      "note": ""
    }
  ]
}
```

> `asof` nel body rispecchia la data richiesta dal client (non quella interna shifted).  
> `transaction_id` è `null` per il backend CSV (nessun row-id nel ledger CSV).

#### Esempio 2: stock alla chiusura del 25 febbraio (mode=END_OF_DAY)

```
GET /stock/PRD-0042?asof_date=2026-02-25&mode=END_OF_DAY
```

```json
{
  "sku": "PRD-0042",
  "description": "Latte intero UHT 1L",
  "asof": "2026-02-25",
  "mode": "END_OF_DAY",
  "on_hand": 36,
  "on_order": 24,
  "unfulfilled_qty": 0,
  "last_event_date": "2026-02-25",
  "recent_transactions": [
    {
      "transaction_id": null,
      "date": "2026-02-25",
      "event": "SALE",
      "qty": 12,
      "receipt_date": null,
      "note": ""
    },
    {
      "transaction_id": null,
      "date": "2026-02-24",
      "event": "RECEIPT",
      "qty": 24,
      "receipt_date": "2026-02-24",
      "note": "OC-20260220-001"
    }
  ]
}
```

> Rispetto a POINT_IN_TIME, `on_hand` è diminuito di 12 (la SALE del 25/02 è inclusa).

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU 'PRD-9999' non trovato nel database.",
    "details": []
  }
}
```

---

### POST /exceptions

Registra un evento di eccezione nel ledger (WASTE, ADJUST, UNFULFILLED).

**Idempotency key**: `date + sku + event` — se esiste già una transazione con la stessa tripletta nella stessa giornata, la richiesta viene **respinta con `409`** (non inserita due volte).

#### Request body

```json
{
  "date": "2026-02-24",
  "sku": "PRD-0042",
  "event": "WASTE",
  "qty": 3,
  "note": "Prodotti scaduti trovati in reparto"
}
```

| Campo | Tipo | Obbligatorio | Valori ammessi |
|---|---|---|---|
| `date` | `string` (date) | ✓ | ISO 8601, non futura di oltre 7 giorni |
| `sku` | `string` | ✓ | SKU esistente nel DB |
| `event` | `string` | ✓ | `WASTE`, `ADJUST`, `UNFULFILLED` |
| `qty` | `integer` | ✓ | ≥ 1 (il segno viene assegnato internamente) |
| `note` | `string` | — | Max 500 caratteri |

#### Response `201 Created`

```json
{
  "transaction_id": 1105,
  "date": "2026-02-24",
  "sku": "PRD-0042",
  "event": "WASTE",
  "qty": 3,
  "note": "Prodotti scaduti trovati in reparto",
  "idempotency_key": "2026-02-24:PRD-0042:WASTE"
}
```

#### Response `400 Bad Request` (campo mancante)

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Il corpo della richiesta contiene campi non validi",
    "details": [
      { "field": "qty", "issue": "Campo obbligatorio mancante" },
      { "field": "event", "issue": "'SALE' non è un tipo di eccezione ammesso; valori validi: WASTE, ADJUST, UNFULFILLED" }
    ]
  }
}
```

#### Response `404 Not Found` (SKU inesistente)

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU 'PRD-9999' non trovato nel database",
    "details": []
  }
}
```

#### Response `409 Conflict` (idempotency key già presente)

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "Eccezione già registrata per questa tripletta date/sku/event",
    "details": [
      {
        "field": "idempotency_key",
        "issue": "Chiave '2026-02-24:PRD-0042:WASTE' già presente (transaction_id=1105)"
      }
    ]
  }
}
```

---

### POST /receipts/close

Chiude un ordine di acquisto registrando gli eventi RECEIPT nel ledger.  
Operazione **idempotente** su due livelli:
- `client_receipt_id` (UUID v4 opzionale) → replay `200` dal record SQLite
- `receipt_id` legacy (già in `receiving_logs`) → replay `200` sintetico

#### Request body

```json
{
  "receipt_id": "2026-02-24_SUPPLIER-A_REC001",
  "receipt_date": "2026-02-24",
  "client_receipt_id": "550e8400-e29b-41d4-a716-446655440000",
  "lines": [
    {
      "sku": "PRD-0042",
      "qty_received": 24,
      "note": "DDT-0042"
    },
    {
      "ean": "9876543210987",
      "qty_received": 6,
      "expiry_date": "2026-08-01",
      "note": "scanner ha letto scadenza"
    },
    {
      "sku": "PRD-0099",
      "qty_received": 0
    }
  ]
}
```

| Campo | Tipo | Obbligatorio | Note |
|---|---|---|---|
| `receipt_id` | `string` | ✓ | Chiave legacy; formato consigliato: `{receipt_date}_{supplier}_{ref}`. Max 100 char. |
| `receipt_date` | `string` (date) | ✓ | Data effettiva di ricezione |
| `client_receipt_id` | `string` | — | UUID v4 per strong idempotency; max 128 char. Se assente nessun record nella tabella idempotenza. |
| `lines` | `array` | ✓ | Almeno 1 riga |
| `lines[].sku` | `string` | —\* | SKU esistente; priorità su `ean` se entrambi presenti |
| `lines[].ean` | `string` | — \* | EAN-12/13 digits-only; risolto → SKU server-side |
| `lines[].qty_received` | `integer` | ✓ | ≥ 0; se 0 → riga registrata come `skipped`, nessun evento RECEIPT |
| `lines[].expiry_date` | `string` (date) | cond. | Obbligatoria se `SKU.has_expiry_label = true` |
| `lines[].note` | `string` | — | Max 200 caratteri |

\* Almeno uno tra `sku` e `ean` è obbligatorio per riga.

#### Response `201 Created` (prima elaborazione)

```json
{
  "receipt_id": "2026-02-24_SUPPLIER-A_REC001",
  "receipt_date": "2026-02-24",
  "already_posted": false,
  "client_receipt_id": "550e8400-e29b-41d4-a716-446655440000",
  "lines": [
    {
      "line_index": 0,
      "sku": "PRD-0042",
      "ean": null,
      "qty_received": 24,
      "expiry_date": null,
      "status": "ok"
    },
    {
      "line_index": 1,
      "sku": "PRD-0055",
      "ean": "9876543210987",
      "qty_received": 6,
      "expiry_date": "2026-08-01",
      "status": "ok"
    },
    {
      "line_index": 2,
      "sku": "PRD-0099",
      "ean": null,
      "qty_received": 0,
      "expiry_date": null,
      "status": "skipped"
    }
  ]
}
```

| Campo risposta | Note |
|---|---|
| `already_posted` | `false` su 201 (prima elaborazione) |
| `client_receipt_id` | Echo del valore inviato, `null` se assente |
| `lines[].line_index` | Indice 0-based della riga nella richiesta |
| `lines[].status` | `ok` = RECEIPT scritto · `skipped` = qty=0, nessun evento · `already_received` = replay |

#### Response `200 OK` (già elaborato — idempotenza)

```json
{
  "receipt_id": "2026-02-24_SUPPLIER-A_REC001",
  "receipt_date": "2026-02-24",
  "already_posted": true,
  "client_receipt_id": "550e8400-e29b-41d4-a716-446655440000",
  "lines": [
    { "line_index": 0, "sku": "PRD-0042", "qty_received": 24, "status": "already_received" },
    { "line_index": 1, "sku": "PRD-0055", "qty_received": 6, "expiry_date": "2026-08-01", "status": "already_received" },
    { "line_index": 2, "sku": "PRD-0099", "qty_received": 0, "status": "already_received" }
  ]
}
```

Le righe del replay hanno sempre `status: "already_received"`. Il corpo è identico a quello della prima risposta `201`, con `already_posted` sovrapposto a `true`.

#### Response `400 Bad Request` (errori per riga)

La validazione è **all-errors-first**: tutte le righe vengono ispezionate prima di produrre errori.

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "2 errori nelle righe della ricevuta; nessuna riga è stata elaborata",
    "details": [
      {
        "field": "lines[0].sku",
        "issue": "SKU 'GHOST' non trovato nel database"
      },
      {
        "field": "lines[1].expiry_date",
        "issue": "expiry_date è obbligatoria per lo SKU PRD-EXP (has_expiry_label=True)"
      }
    ]
  }
}
```

> **Atomicità**: se anche una sola riga fallisce la validazione, *nessuna* riga viene scritta nel ledger.

---

## 5. Regole di idempotenza

| Endpoint | Chiave di idempotenza | Comportamento duplicato |
|---|---|---|
| `POST /exceptions` | `client_event_id` (UUID) | `200 OK` replay verbatim |
| `POST /exceptions` | `date + sku + event` (legacy) | `409 Conflict` |
| `POST /receipts/close` | `client_receipt_id` (UUID) | `200 OK` replay verbatim, `already_posted: true` |
| `POST /receipts/close` | `receipt_id` in `receiving_logs` (legacy) | `200 OK` sintetico, `already_posted: true` |

La chiave `client_receipt_id` (UUID v4) garantisce idempotenza forte via tabella SQLite `api_idempotency_keys`.  
La chiave `receipt_id` garantisce idempotenza legacy tramite lookup in `receiving_logs`.

Formato consigliato per `receipt_id` (legacy key):

Formato consigliato per `receipt_id`:

```
{receipt_date}_{supplier_code}_{document_ref}
# Esempio: 2026-02-24_SUPPLIER-A_DDT-2024-00042
```

---

## 6. Validazione e vincoli

### Date

- Formato obbligatorio: `YYYY-MM-DD`
- Date future > 7 giorni rifiutate per eventi WASTE/ADJUST/UNFULFILLED
- `asof_date` per le query stock: accettata qualsiasi data passata o presente
- Semantica `asof_date` in `GET /stock/{sku}` dipende dal parametro `mode`:
  - `POINT_IN_TIME` (default): `date < asof_date` — apertura giornata
  - `END_OF_DAY`: `date <= asof_date` — chiusura giornata (traslazione +1d interna)

### EAN

- Formati accettati: EAN-12 (12 cifre) ed EAN-13 (13 cifre). EAN-8 **non supportato**.
- Solo caratteri numerici (0-9); lettere o simboli → `400 BAD_REQUEST`
- EAN malformato in lookup → `400 BAD_REQUEST` con messaggio descrittivo
- EAN malformato già presente nel DB (legacy) → restituito con `ean_valid: false`, warning loggato, **mai crash**

### Quantità

- `POST /exceptions`: `qty` ≥ 1 (intero positivo)
- `POST /receipts/close`: `qty_received` ≥ 0; valore 0 = riga `skipped` (nessun evento RECEIPT scritto, ma receiving_log aggiornato)
- Il segno viene assegnato internamente in base all'event type:
  - WASTE, SALE → decremento `on_hand`
  - ADJUST → set assoluto `on_hand`
  - ORDER → incremento `on_order`
  - RECEIPT → decremento `on_order`, incremento `on_hand`

### Lunghezze stringa

| Campo | Max lunghezza |
|---|---|
| `sku` | 50 caratteri |
| `receipt_id` | 100 caratteri |
| `note` (eccezioni) | 500 caratteri |
| `note` (righe ricevuta) | 200 caratteri |

---

## Vedere anche

- [docs/config.md](config.md) — variabili d'ambiente del backend
- [docs/runbook.md](runbook.md) — avvio, backup, troubleshooting
- `backend/dos_backend/domain/models.py` — definizione dei tipi EventType, SKU, Transaction, Stock
- `backend/dos_backend/domain/ledger.py` — logica AsOf di calcolo stock (StockCalculator)
- `backend/dos_backend/routers/stock.py` — implementazione `GET /stock/{sku}` con mode POINT_IN_TIME/END_OF_DAY
- `backend/dos_backend/schemas.py` — StockMode, StockDetailResponse, TransactionSummary
