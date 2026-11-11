# mmm_ready

Paquete dbt open source que convierte datos de marketing en una **tabla lista para MMM**
(Meridian, PyMC-Marketing, Robyn) y en un **diagnóstico de calidad** que dice si esos datos
sirven para modelar.

## Qué hace
- `fct_marketing_spend_daily`: inversión diaria por geo y canal MMM, mapeada con una taxonomía canónica.
- `fct_outcomes_daily`: KPI diario con precio, promociones y festivos.
- `mmm_input_weekly`: tabla ancha semana × geo, una columna de inversión e impresiones por canal.
- `mmm_readiness_report`: por canal, `ready` / `review` / `blocked` (histórico, huecos, inversión sin mapear,
  canales escasos, poca variación, picos, colinealidad).
- `mmm_channel_correlations`: correlación de inversión entre pares de canales.

## Arranque local (DuckDB, coste cero)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cd integration_tests
python ../scripts/generate_synthetic_data.py --out seeds
export DBT_PROFILES_DIR=.
dbt deps && dbt seed && dbt build
```
Los datos sintéticos traen errores sembrados a propósito (ver `ground_truth.json`); el informe de readiness debe detectarlos.

## Usarlo con tus datos
Tus tablas de origen necesitan estas columnas:
- `ad_spend_daily`: date, geo, platform, campaign_type, spend, impressions, clicks
- `outcomes_daily`: date, geo, revenue, conversions
- `controls_daily`: date, geo, avg_price, promo_flag

Apunta a ellas con variables en tu `dbt_project.yml`:
```yaml
vars:
  mmm_ready_raw_schema: marketing_raw
  mmm_ready_ad_spend_identifier: my_ad_spend_table
```
Y amplía `seeds/channel_taxonomy.csv` con tus plataformas y tipos de campaña.

## Notas
- Semana: se usa `date_trunc('week')` del warehouse; el inicio de semana puede variar (BigQuery empieza en domingo).
- Probado en DuckDB. Databricks y Snowflake: pendiente de validar.

Licencia Apache 2.0.
