"""PostgreSQL capture runs in the business transaction, including bulk SQL."""
from sqlalchemy import text

INSTALL_SQL = r"""
CREATE OR REPLACE FUNCTION public.jc_audit_redact(value jsonb, field_name text DEFAULT '')
RETURNS jsonb LANGUAGE plpgsql AS $body$
DECLARE result jsonb; item record; cleaned text;
BEGIN
  IF field_name ~* '(password|passwd|secret|token|authorization|cookie|credential|cipher|private.?key|api.?key|aes.?key|signature|binding_id)' THEN
    RETURN to_jsonb('已隐藏'::text);
  END IF;
  IF jsonb_typeof(value) = 'object' THEN
    result := '{}'::jsonb;
    FOR item IN SELECT key,val FROM jsonb_each(value) AS e(key,val) LOOP
      result := result || jsonb_build_object(item.key, public.jc_audit_redact(item.val,item.key));
    END LOOP;
    RETURN result;
  ELSIF jsonb_typeof(value) = 'array' THEN
    RETURN COALESCE((SELECT jsonb_agg(public.jc_audit_redact(v)) FROM jsonb_array_elements(value) AS e(v)), '[]'::jsonb);
  ELSIF jsonb_typeof(value) = 'string' THEN
    cleaned := value #>> '{}';
    cleaned := regexp_replace(cleaned, '(?<![0-9])([0-9]{6})[0-9]{8}([0-9]{3}[0-9Xx])(?![0-9])', '\1********\2', 'g');
    cleaned := regexp_replace(cleaned, '(?<![0-9])(1[3-9][0-9])[0-9]{4}([0-9]{4})(?![0-9])', '\1****\2', 'g');
    cleaned := regexp_replace(cleaned, '((access_token|token|secret|signature|scode|password)=)[^&[:space:]]+', '\1***', 'gi');
    RETURN to_jsonb(cleaned);
  END IF;
  RETURN value;
END;
$body$;

CREATE OR REPLACE FUNCTION public.jc_audit_capture()
RETURNS trigger LANGUAGE plpgsql AS $body$
DECLARE
  prior jsonb; following jsonb; keys jsonb; ids jsonb := '{}'::jsonb;
  field text; trace text; actor text; name text; account_name text; origin text; area text; act text;
BEGIN
  prior := CASE WHEN TG_OP='INSERT' THEN NULL ELSE to_jsonb(OLD) END;
  following := CASE WHEN TG_OP='DELETE' THEN NULL ELSE to_jsonb(NEW) END;
  IF TG_OP='UPDATE' AND prior IS NOT DISTINCT FROM following THEN RETURN NEW; END IF;
  SELECT jsonb_agg(key ORDER BY key) INTO keys FROM (
    SELECT k AS key FROM jsonb_object_keys(COALESCE(following,prior)) AS x(k)
    WHERE TG_OP <> 'UPDATE' OR prior->k IS DISTINCT FROM following->k
  ) AS diff;
  IF keys IS NULL THEN RETURN COALESCE(NEW,OLD); END IF;
  FOR field IN SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
              WHERE i.indrelid=TG_RELID AND i.indisprimary LOOP
    ids := ids || jsonb_build_object(field,COALESCE(following,prior)->field);
  END LOOP;
  trace := NULLIF(current_setting('jc.audit_event',true),'');
  actor := NULLIF(current_setting('jc.audit_actor',true),'');
  name := NULLIF(current_setting('jc.audit_name',true),'');
  account_name := NULLIF(current_setting('jc.audit_account',true),'');
  origin := COALESCE(NULLIF(current_setting('jc.audit_source',true),''),'database');
  area := COALESCE(NULLIF(current_setting('jc.audit_module',true),''),TG_TABLE_NAME);
  act := COALESCE(NULLIF(current_setting('jc.audit_action',true),''),'数据库变更');
  trace := COALESCE(trace,'db:'||txid_current()::text);
  INSERT INTO public.audit_event(event_key,actor_id,actor_name,account,source,module,action,outcome,created_at)
    VALUES(trace,CASE WHEN actor ~ '^[0-9]+$' THEN actor::bigint ELSE NULL END,
           COALESCE(name,CASE WHEN origin='database' THEN '数据库连接' ELSE '系统任务' END),
           COALESCE(account_name,CASE WHEN origin='database' THEN session_user ELSE NULL END),
           origin,area,act,CASE WHEN origin='database' THEN 'success' ELSE 'running' END,clock_timestamp())
    ON CONFLICT(event_key) DO NOTHING;
  INSERT INTO public.audit_change(event_key,table_name,record_key,operation,before,after,fields,created_at)
    VALUES(trace,TG_TABLE_NAME,ids::text,lower(TG_OP),public.jc_audit_redact(prior),public.jc_audit_redact(following),keys,clock_timestamp());
  RETURN COALESCE(NEW,OLD);
END;
$body$;

CREATE OR REPLACE FUNCTION public.jc_audit_install()
RETURNS integer LANGUAGE plpgsql AS $body$
DECLARE item record; installed integer := 0;
BEGIN
  FOR item IN SELECT c.oid,c.relname,n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind='r'
      AND c.relname NOT LIKE 'audit_%' AND c.relname NOT LIKE 'log_%'
      AND c.relname NOT IN ('alembic_version','sync_job_log','sys_wecom_api_call')
  LOOP
    IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=item.oid AND tgname='jc_operation_audit' AND NOT tgisinternal) THEN
      EXECUTE format('CREATE TRIGGER jc_operation_audit AFTER INSERT OR UPDATE OR DELETE ON %I.%I FOR EACH ROW EXECUTE FUNCTION public.jc_audit_capture()',item.nspname,item.relname);
      installed := installed+1;
    END IF;
  END LOOP;
  RETURN installed;
END;
$body$;
"""


def install(connection):
    if connection.dialect.name != "postgresql":
        raise RuntimeError("操作审计需要当前 PostgreSQL 数据库")
    connection.execute(text(INSTALL_SQL))
    connection.exec_driver_sql("SELECT public.jc_audit_install()")
