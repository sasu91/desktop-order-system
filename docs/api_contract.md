# API Contract — desktop-order-system

> **Fonte di verità**: [`docs/openapi.json`](openapi.json) generato da FastAPI.  
> Aggiornato automaticamente in CI da `python tools/export_openapi.py`.  
> Versione API attuale: **0.1.0** — schema OpenAPI 3.1.0

---

## Indice

1. [Convenzioni generali](#1-convenzioni-generali)
2. [Autenticazione](#2-autenticazione)
3. [Formato errori](#3-formato-errori)
4. [Tabella di riferimento rapido](#4-tabella-di-riferimento-rapido)
5. [Endpoints](#5-endpoints)
   - [GET /health](#get-health)
   - [GET /api/v1/skus/by-ean/{ean}](#get-apiv1skusby-eanean)
   - [GET /api/v1/stock](#get-apiv1stock)
   - [GET /api/v1/stock/{sku}](#get-apiv1stocksku)
   - [POST /api/v1/exceptions](#post-apiv1exceptions)
   - [POST /api/v1/exceptions/daily-upsert](#post-apiv1exceptionsdaily-upsert)
   - [POST /api/v1/receipts/close](#post-apiv1receiptsclose)
6. [Regole di idempotenza](#6-regole-di-idempotenza)
7. [Validazione e vincoli](#7-validazione-e-vincoli)

---

## 1. Convenzioni generali

| Proprietà | Valore |
|---|---|
| Base URL | `http://127.0.0.1:8000` |
| Prefisso versioned | `/api/v1` (tutti gli endpoint tranne `/health`) |
| Formato corpo | `application/json` (UTF-8) |
| Date | ISO 8601 `YYYY-MM-DD` (stringa) |
| Documentazione interattiva | `GET /api/docs` (Swagger UI) · `GET /api/redoc` |
| Schema OpenAPI raw | `GET /api/openapi.json` |

---

## 2. Autenticazione

Bearer token statico letto da `DOS_API_TOKEN` (variabile d'ambiente).

```
Authorization: Bearer <DOS_API_TOKEN>
```

- Token assente o errato → `401 Unauthorized`
- `GET /health` è **pubblico** — non richiede token
- Se `DOS_API_TOKEN` non è configurato: `dev_mode=true`, tutti gli endpoint sono accessibili senza token (solo per sviluppo locale — **non usare in produzione**)

---

## 3. Formato errori

Tutti gli errori applicativi seguono questa struttura:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU \'PRD-9999\' non trovato nel database.",
    "details": []
  }
}
```

`details` è un array di oggetti `{ "field": "...", "issue": "..." }`, popolato quando ci sono errori per campo (es. validazione righe di una ricevuta).

Gli errori di validazione Pydantic (422) usano il formato FastAPI standard:
```json
{ "detail": [{ "loc": ["body", "qty"], "msg": "...", "type": "..." }] }
```

### Codici HTTP

| HTTP | `error.code` | Quando |
|---|---|---|
| `400` | `BAD_REQUEST` / `VALIDATION_ERROR` | Campo mancante, tipo errato, EAN non valido, vincoli di formato |
| `401` | `UNAUTHORIZED` | Token assente o non valido |
| `404` | `NOT_FOUND` | SKU o risorsa inesistente |
| `422` | — (FastAPI standard) | Tipo Pydantic non rispettato (es. `"SUPEREVENT"` per `event`) |
| `500` | `INTERNAL_ERROR` | Errore non gestito lato server |

> **Nota**: non esiste più `409 Conflict` su `POST /exceptions`. La tripletta `date+sku+event` non è più una chiave di idempotenza. Vedere [§6](#6-regole-di-idempotenza).

---

## 4. Tabella di riferimento rapido

| Metodo + Percorso | Auth | Request | Response successo | Errori possibili |
|---|:---:|---|---|---|
| `GET /health` | ✗ | — | `200` `HealthResponse` | — |
| `GET /api/v1/skus/by-ean/{ean}` | ✓ | path: `ean` | `200` `SKUResponse` | `400` EAN non valido · `404` non trovato |
| `GET /api/v1/stock` | ✓ | query: `asof_date`, `mode`, `sku[]`, `in_assortment`, `page`, `page_size` | `200` `StockListResponse` | `422` param non valido |
| `GET /api/v1/stock/{sku}` | ✓ | path: `sku` · query: `asof_date`, `mode`, `recent_n` | `200` `StockDetailResponse` | `404` SKU non trovato · `422` param non valido |
| `POST /api/v1/exceptions` | ✓ | `ExceptionRequest` | `201` `ExceptionResponse` · `200` (replay `client_event_id`) | `400` input non valido · `404` SKU · `422` event non valido |
| `POST /api/v1/exceptions/daily-upsert` | ✓ | `DailyUpsertRequest` | `200` `DailyUpsertResponse` | `400` input non valido · `404` SKU · `422` event/mode non valido |
| `POST /api/v1/receipts/close` | ✓ | `ReceiptsCloseRequest` | `201` `ReceiptsCloseResponse` · `200` (replay) | `400` errori per riga · `422` struttura non valida |

---

## 5. Endpoints

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
  "timestamp": "2026-02-25T10:30:00Z"
}
```

| Campo | Tipo | Note |
|---|---|---|
| `status` | `"ok"` \| `"degraded"` | `"degraded"` se il DB non è raggiungibile; la risposta resta sempre `200` |
| `version` | `string` | Versione del pacchetto `dos-backend` |
| `db_path` | `string` | Percorso assoluto del file SQLite |
| `db_reachable` | `boolean` | `false` se la connessione SQLite fallisce |
| `storage_backend` | `string` | `"sqlite"` o `"csv"` |
| `dev_mode` | `boolean` | `true` se `DOS_API_TOKEN` non è configurato |
| `timestamp` | `string` | ISO 8601 UTC al momento della risposta |

---

### GET /api/v1/skus/by-ean/{ean}

Cerca uno SKU tramite codice EAN. Utile per client che usano scanner barcode.

**Autenticazione**: richiesta

**Parametri path**

| Param | Tipo | Note |
|---|---|---|
| `ean` | `string` | EAN-12 o EAN-13 (solo cifre). EAN-8 non supportato. |

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

| Campo | Default | Note |
|---|---|---|
| `ean_valid` | `true` | `false` se il valore EAN nel DB ha formato irregolare (dato legacy — mai crash) |
| `moq` | `1` | Minimum order quantity |
| `pack_size` | `1` | Unità per collo |
| `lead_time_days` | `7` | Giorni di consegna stimati |
| `safety_stock` | `0` | Giacenza di sicurezza |
| `shelf_life_days` | `0` | Vita a scaffale in giorni (0 = non applicabile) |
| `in_assortment` | `true` | `false` = SKU dismesso |
| `category` | `""` | Categoria merceologica |
| `department` | `""` | Reparto |

#### Response `400 Bad Request` — EAN non valido

```json
{
  "error": {
    "code": "BAD_REQUEST",
    "message": "EAN non valido: \'12345\' — attesi 12 o 13 digit numerici",
    "details": []
  }
}
```

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Nessun SKU trovato per EAN \'8001234567890\'",
    "details": []
  }
}
```

---

### GET /api/v1/stock

Stock calcolato ledger-AsOf per tutti gli SKU, con paginazione.

**Autenticazione**: richiesta

**Query parameters**

| Param | Tipo | Default | Note |
|---|---|---|---|
| `asof_date` | `string` (YYYY-MM-DD) | oggi | Data di calcolo. Semantica: `date < asof_date` (POINT_IN_TIME) o `date ≤ asof_date` (END_OF_DAY). |
| `mode` | `POINT_IN_TIME` \| `END_OF_DAY` | `POINT_IN_TIME` | Vedi tabella semantica sotto. |
| `sku` | `string` (ripetibile) | tutti | Filtra su uno o più SKU: `?sku=PRD-001&sku=PRD-002` |
| `in_assortment` | `boolean` | `true` | `false` per includere anche SKU dismessi |
| `page` | `integer` ≥ 1 | `1` | Pagina (1-based) |
| `page_size` | `integer` 1–200 | `50` | Righe per pagina |

**Semantica `mode`**

| `mode` | Condizione interna | Significato pratico |
|---|---|---|
| `POINT_IN_TIME` | `date < asof_date` | Stock all\'**apertura** di `asof_date`; eventi del giorno stesso esclusi |
| `END_OF_DAY` | `date < asof_date + 1d` | Stock alla **chiusura** di `asof_date`; eventi del giorno stesso inclusi |

#### Response `200 OK`

```json
{
  "asof": "2026-02-25",
  "page": 1,
  "page_size": 50,
  "total": 2,
  "items": [
    {
      "sku": "PRD-0042",
      "description": "Latte intero UHT 1L",
      "on_hand": 48,
      "on_order": 24,
      "last_event_date": "2026-02-24"
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

| Campo | Note |
|---|---|
| `asof` | Data AsOf usata nel calcolo (riflette `asof_date` della query) |
| `total` | Totale SKU nel risultato (prima della paginazione) |
| `items[].last_event_date` | Data dell\'ultimo evento nel ledger per lo SKU; `null` se nessun evento |

---

### GET /api/v1/stock/{sku}

Stock calcolato ledger-AsOf per un singolo SKU, con dettaglio delle ultime transazioni.

**Autenticazione**: richiesta

**Parametri path**

| Param | Tipo | Note |
|---|---|---|
| `sku` | `string` | Codice SKU esatto (case-sensitive) |

**Query parameters**

| Param | Tipo | Default | Note |
|---|---|---|---|
| `asof_date` | `string` (YYYY-MM-DD) | oggi | Data di calcolo (vedi semantica `mode`) |
| `mode` | `POINT_IN_TIME` \| `END_OF_DAY` | `POINT_IN_TIME` | Stessa semantica di `GET /stock` |
| `recent_n` | `integer` 0–200 | `20` | Numero di transazioni recenti da restituire |

#### Response `200 OK`

```
GET /api/v1/stock/PRD-0042?asof_date=2026-02-25&mode=POINT_IN_TIME&recent_n=3
```

```json
{
  "sku": "PRD-0042",
  "description": "Latte intero UHT 1L",
  "on_hand": 48,
  "on_order": 24,
  "asof": "2026-02-25",
  "mode": "POINT_IN_TIME",
  "unfulfilled_qty": 0,
  "last_event_date": "2026-02-24",
  "recent_transactions": [
    {
      "transaction_id": 1104,
      "date": "2026-02-24",
      "event": "RECEIPT",
      "qty": 24,
      "receipt_date": "2026-02-24",
      "note": "OC-20260220-001"
    },
    {
      "transaction_id": 1103,
      "date": "2026-02-24",
      "event": "SALE",
      "qty": 12,
      "receipt_date": null,
      "note": ""
    }
  ]
}
```

| Campo | Note |
|---|---|
| `asof` | Rispecchia `asof_date` del query param (non la data interna shifted) |
| `unfulfilled_qty` | Totale unità UNFULFILLED non evase per lo SKU |
| `last_event_date` | Data più recente nel ledger per lo SKU; `null` se nessun evento |
| `recent_transactions[].transaction_id` | `null` per il backend CSV (nessun row-id) |

#### Esempio END_OF_DAY — stock alla chiusura del 25 febbraio

```
GET /api/v1/stock/PRD-0042?asof_date=2026-02-25&mode=END_OF_DAY
```

```json
{
  "sku": "PRD-0042",
  "on_hand": 36,
  "on_order": 24,
  "asof": "2026-02-25",
  "mode": "END_OF_DAY"
}
```

> `on_hand` è 12 unità in meno rispetto a POINT_IN_TIME: la vendita del 25/02 è ora inclusa nel calcolo.

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU \'PRD-9999\' non trovato nel database.",
    "details": []
  }
}
```

---

### POST /api/v1/exceptions

Registra un evento discreto (WASTE, ADJUST, UNFULFILLED) nel ledger.  
Ogni chiamata **aggiunge sempre una nuova riga**: più eventi nello stesso giorno sono legittimi (es. due scarti separati).

**Autenticazione**: richiesta

#### Request body — `ExceptionRequest`

```json
{
  "date": "2026-02-25",
  "sku": "PRD-0042",
  "event": "WASTE",
  "qty": 3,
  "note": "Prodotti scaduti trovati in reparto",
  "client_event_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

| Campo | Tipo | Req | Constraint | Note |
|---|---|:---:|---|---|
| `date` | `string` (date) | ✓ | ISO 8601 | Data dell\'evento |
| `sku` | `string` | ✓ | SKU esistente | Case-sensitive |
| `event` | `string` | ✓ | `WASTE` \| `ADJUST` \| `UNFULFILLED` | |
| `qty` | `integer` | ✓ | ≥ 1 | Unità (segno assegnato internamente) |
| `note` | `string` | — | max 500 car. | Valore default `""` |
| `client_event_id` | `string` | — | 1–128 car. | UUID per idempotenza forte. Se assente: nessuna deduplica. |

#### Response `201 Created` — primo inserimento

```json
{
  "transaction_id": null,
  "date": "2026-02-25",
  "sku": "PRD-0042",
  "event": "WASTE",
  "qty": 3,
  "note": "Prodotti scaduti trovati in reparto",
  "idempotency_key": "550e8400-e29b-41d4-a716-446655440000",
  "already_recorded": false,
  "client_event_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

#### Response `200 OK` — replay idempotente (`client_event_id` già visto)

```json
{
  "already_recorded": true,
  "client_event_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

> Il corpo è identico alla prima risposta `201`, con `already_recorded` forzato a `true`.  
> **Nessuna scrittura** nel ledger.

| Campo | Note |
|---|---|
| `transaction_id` | `null` per backend CSV (nessun row-id); intero per SQLite |
| `idempotency_key` | Echo del `client_event_id`; `null` se non fornito |
| `already_recorded` | `false` su 201; `true` su 200 (replay) |

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU \'PRD-9999\' non trovato nel database.",
    "details": []
  }
}
```

> **Comportamento senza `client_event_id`**: ogni richiesta viene sempre accettata (`201`).  
> Non esiste una deduplica automatica su `date+sku+event`. Per flussi ERP che inviano totali cumulativi usare [`POST /api/v1/exceptions/daily-upsert`](#post-apiv1exceptionsdaily-upsert).

---

### POST /api/v1/exceptions/daily-upsert

Mantieni un **unico totale giornaliero** per la tripletta `(sku, date, event)`.  
Progettato per integrazioni ERP/POS che inviano il totale cumulativo di fine giornata.

**Autenticazione**: richiesta

> **Differenza con `POST /exceptions`**
>
> | Endpoint | Semantica | Idempotenza |
> |---|---|---|
> | `POST /exceptions` | Aggiunge *sempre* un nuovo evento discreto | `client_event_id` (UUID opzionale) |
> | `POST /exceptions/daily-upsert` | Gestisce un unico totale per `(sku, date, event)` | `replace` mode è inherentemente idempotente |

#### Request body — `DailyUpsertRequest`

```json
{
  "date": "2026-02-25",
  "sku": "PRD-0042",
  "event": "WASTE",
  "qty": 10,
  "mode": "replace",
  "note": "Totale scarti EOD da POS"
}
```

| Campo | Tipo | Req | Constraint | Note |
|---|---|:---:|---|---|
| `date` | `string` (date) | ✓ | ISO 8601 | |
| `sku` | `string` | ✓ | SKU esistente | |
| `event` | `string` | ✓ | `WASTE` \| `ADJUST` \| `UNFULFILLED` | |
| `qty` | `integer` | ✓ | ≥ 1 | Totale da impostare (replace) o delta da aggiungere (sum) |
| `mode` | `string` | — | `replace` \| `sum` | Default `"replace"` |
| `note` | `string` | — | max 500 car. | Default `""` |

**Semantica `mode`**

| `mode` | Comportamento | Idempotenza |
|---|---|---|
| `replace` | Imposta il totale giornaliero a esattamente `qty`. Se il totale corrente è già `qty` → risponde `noop=true` senza toccare il ledger. | ✓ Inherentemente idempotente |
| `sum` | Aggiunge `qty` come delta al totale corrente. Preserva le singole righe nel ledger (audit trail). | ✗ Additivo per design |

#### Response `200 OK`

**replace — primo inserimento (totale era 0)**
```json
{
  "date": "2026-02-25",
  "sku": "PRD-0042",
  "event": "WASTE",
  "mode": "replace",
  "qty_delta": 10,
  "qty_total": 10,
  "note": "Totale scarti EOD da POS",
  "noop": false
}
```

**replace — noop (totale già uguale a `qty`)**
```json
{
  "date": "2026-02-25",
  "sku": "PRD-0042",
  "event": "WASTE",
  "mode": "replace",
  "qty_delta": 0,
  "qty_total": 10,
  "note": "",
  "noop": true
}
```

**sum — accumulo (totale precedente era 7)**
```json
{
  "date": "2026-02-25",
  "sku": "PRD-0042",
  "event": "WASTE",
  "mode": "sum",
  "qty_delta": 3,
  "qty_total": 10,
  "note": "",
  "noop": false
}
```

| Campo | Note |
|---|---|
| `qty_delta` | Unità effettivamente scritte nel ledger (0 se noop; negativo se replace ha ridotto il totale) |
| `qty_total` | Totale corrente per `(sku, date, event)` dopo questa chiamata |
| `noop` | `true` solo in mode=replace quando il totale era già uguale a `qty` |

#### Response `404 Not Found`

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "SKU \'PRD-9999\' non trovato nel database.",
    "details": []
  }
}
```

---

### POST /api/v1/receipts/close

Registra gli eventi RECEIPT nel ledger per ogni riga di merce ricevuta.

**Autenticazione**: richiesta

**Atomicità**: se anche una sola riga fallisce la validazione, *nessuna* riga viene scritta; la risposta `400` elenca tutti gli errori con indice, campo e motivo.

**Idempotenza su due livelli**:
- `client_receipt_id` (UUID): lookup in `api_idempotency_keys` → `200 already_posted=true`, ledger invariato
- `receipt_id` legacy: lookup in `receiving_logs` → `200 already_posted=true` (risposta sintetica)

#### Request body — `ReceiptsCloseRequest`

```json
{
  "receipt_id": "2026-02-25_SUPPLIER-A_DDT-001",
  "receipt_date": "2026-02-25",
  "client_receipt_id": "550e8400-e29b-41d4-a716-446655440000",
  "lines": [
    {
      "sku": "PRD-0042",
      "qty_received": 24,
      "note": "collo integro"
    },
    {
      "ean": "9780201379624",
      "qty_received": 6,
      "expiry_date": "2026-09-01"
    },
    {
      "sku": "PRD-0099",
      "qty_received": 0
    }
  ]
}
```

**Campi radice**

| Campo | Tipo | Req | Constraint | Note |
|---|---|:---:|---|---|
| `receipt_id` | `string` | ✓ | max 100 car. | Chiave legacy; formato consigliato: `{date}_{supplier}_{ref}` |
| `receipt_date` | `string` (date) | ✓ | ISO 8601 | Data effettiva di ricezione |
| `lines` | `array` | ✓ | ≥ 1 riga | |
| `client_receipt_id` | `string` | — | 1–128 car. | UUID per idempotenza forte |

**Campi riga — `ReceiptLine`**

| Campo | Tipo | Req | Constraint | Note |
|---|---|:---:|---|---|
| `qty_received` | `integer` | ✓ | ≥ 0 | 0 = riga `skipped`, nessun evento RECEIPT scritto |
| `sku` | `string` | ✗* | SKU esistente | Priorità su `ean` se entrambi presenti |
| `ean` | `string` | ✗* | EAN-12/13 cifre | Risolto → SKU server-side |
| `expiry_date` | `string` (date) | cond. | ISO 8601 | Obbligatoria se `SKU.has_expiry_label = true` |
| `note` | `string` | — | max 200 car. | Default `""` |

\* Almeno uno tra `sku` e `ean` è obbligatorio per riga.

#### Response `201 Created` — prima elaborazione

```json
{
  "receipt_id": "2026-02-25_SUPPLIER-A_DDT-001",
  "receipt_date": "2026-02-25",
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
      "sku": "PRD-EXP",
      "ean": "9780201379624",
      "qty_received": 6,
      "expiry_date": "2026-09-01",
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

**Valori di `lines[].status`**

| Valore | Significato |
|---|---|
| `ok` | Evento RECEIPT scritto nel ledger |
| `skipped` | `qty_received = 0`: riga registrata nei receiving_logs ma nessun evento RECEIPT |
| `already_received` | Risposta di replay (solo su `200`) |

#### Response `200 OK` — già elaborato (idempotenza)

```json
{
  "receipt_id": "2026-02-25_SUPPLIER-A_DDT-001",
  "receipt_date": "2026-02-25",
  "already_posted": true,
  "client_receipt_id": "550e8400-e29b-41d4-a716-446655440000",
  "lines": [
    { "line_index": 0, "sku": "PRD-0042", "qty_received": 24, "status": "already_received" },
    { "line_index": 1, "sku": "PRD-EXP",  "qty_received": 6,  "status": "already_received" },
    { "line_index": 2, "sku": "PRD-0099", "qty_received": 0,  "status": "already_received" }
  ]
}
```

#### Response `400 Bad Request` — errori per riga (validazione all-errors-first)

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "2 errore/i di validazione — nessuna riga è stata scritta nel ledger.",
    "details": [
      { "field": "lines[0].sku", "issue": "SKU \'GHOST\' non trovato nel database" },
      { "field": "lines[1].expiry_date", "issue": "expiry_date obbligatoria per SKU PRD-EXP (has_expiry_label=true)" }
    ]
  }
}
```

---

## 6. Regole di idempotenza

| Endpoint | Chiave | Meccanismo | Risposta duplicato |
|---|---|---|---|
| `POST /exceptions` | `client_event_id` (UUID, opzionale) | Tabella SQLite `api_idempotency_keys` — claim-first | `200` replay verbatim, `already_recorded=true` |
| `POST /exceptions` | nessuna (assente `client_event_id`) | Ogni chiamata accettata | `201` sempre |
| `POST /exceptions/daily-upsert` | `(sku, date, event)` implicita | `replace` mode — noop se totale invariato | `200` `noop=true`, ledger invariato |
| `POST /receipts/close` | `client_receipt_id` (UUID, opzionale) | Tabella SQLite `api_idempotency_keys` — claim-first | `200` replay verbatim, `already_posted=true` |
| `POST /receipts/close` | `receipt_id` (sempre presente) | Lookup in `receiving_logs` (idempotenza legacy) | `200` sintetico, `already_posted=true` |

**Garanzia di concorrenza (claim-first)**  
Quando due richieste con lo stesso `client_event_id` o `client_receipt_id` arrivano simultaneamente:
1. `INSERT OR IGNORE` su `api_idempotency_keys` con stato pending (`status_code=0`) — una sola ha successo
2. Il vincitore scrive nel ledger e chiama `finalize()` per aggiornare la riga con la risposta reale
3. Il perdente esegue `lookup_with_wait()` (polling fino a 10 × 20 ms) e restituisce la risposta del vincitore con `already_recorded=true`

**Formato consigliato per `receipt_id`** (chiave legacy):
```
{receipt_date}_{supplier_code}_{document_ref}
# Esempio: 2026-02-25_SUPPLIER-A_DDT-2026-001
```

---

## 7. Validazione e vincoli

### Date

- Formato obbligatorio: `YYYY-MM-DD`
- `asof_date` per le query stock: qualsiasi data passata o presente; default = oggi
- Semantica di `asof_date` dipende da `mode`:
  - `POINT_IN_TIME` (default): `date < asof_date` — stock **all\'apertura** di `asof_date`
  - `END_OF_DAY`: `date ≤ asof_date` (internamente: `date < asof_date + 1d`) — stock **alla chiusura**

### EAN

- Accettati: EAN-12 (12 cifre) e EAN-13 (13 cifre). EAN-8 **non supportato**.
- Solo caratteri numerici (0–9); lettere o simboli → `400 BAD_REQUEST`
- EAN malformato in lookup → `400 BAD_REQUEST` con messaggio descrittivo
- EAN malformato già presente nel DB (dato legacy) → restituito con `ean_valid: false`, warning loggato, **mai crash**

### Quantità

| Endpoint/campo | Vincolo |
|---|---|
| `ExceptionRequest.qty` | `integer ≥ 1` |
| `DailyUpsertRequest.qty` | `integer ≥ 1` |
| `ReceiptLine.qty_received` | `integer ≥ 0` (0 → riga `skipped`) |

Il segno degli eventi è assegnato internamente dalla logica del ledger:

| Event | Effetto su `on_hand` | Effetto su `on_order` |
|---|---|---|
| `SNAPSHOT` | `on_hand := qty` (reset assoluto) | — |
| `SALE` | `on_hand -= qty` | — |
| `WASTE` | `on_hand -= qty` | — |
| `ADJUST` | `on_hand := qty` (set assoluto) | — |
| `ORDER` | — | `on_order += qty` |
| `RECEIPT` | `on_hand += qty` | `on_order -= qty` |
| `UNFULFILLED` | nessun effetto (solo tracking) | — |

### Lunghezze stringa

| Campo | Max |
|---|---|
| `receipt_id` | 100 car. |
| `client_event_id` / `client_receipt_id` | 128 car. (min 1) |
| `note` (eccezioni) | 500 car. |
| `note` (righe ricevuta) | 200 car. |

---

## Vedere anche

- [`docs/openapi.json`](openapi.json) — schema OpenAPI 3.1.0 (fonte di verità, generato da CI)
- [`tools/export_openapi.py`](../tools/export_openapi.py) — script per ri-generare/verificare `docs/openapi.json`
- [`backend/dos_backend/schemas.py`](../backend/dos_backend/schemas.py) — modelli Pydantic (ExceptionRequest, DailyUpsertRequest, ReceiptsCloseRequest, …)
- [`backend/dos_backend/domain/ledger.py`](../backend/dos_backend/domain/ledger.py) — logica AsOf di calcolo stock (StockCalculator)
- [`backend/dos_backend/api/idempotency.py`](../backend/dos_backend/api/idempotency.py) — implementazione claim-first idempotency
- [`docs/config.md`](config.md) — variabili d\'ambiente del backend
