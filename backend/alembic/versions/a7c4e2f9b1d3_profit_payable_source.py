"""项目利润口径纳入报表待支付应付快照。"""
from alembic import op

revision = "a7c4e2f9b1d3"
down_revision = "ef42c1b08d7a"
branch_labels = None
depends_on = None


def upgrade():
    """已有 caliber.profit 配置追加 report_payable，未配置的实例走代码默认值。"""
    op.execute("""
        UPDATE sys_dict
           SET dict_value = jsonb_set(dict_value::jsonb, '{cost_sources}',
                                      (dict_value::jsonb -> 'cost_sources') || to_jsonb('report_payable'::text), true)::json
         WHERE dict_type = 'caliber' AND dict_key = 'profit'
           AND jsonb_typeof(dict_value::jsonb -> 'cost_sources') = 'array'
           AND NOT (dict_value::jsonb -> 'cost_sources' ? 'report_payable')
    """)


def downgrade():
    op.execute("""
        UPDATE sys_dict
           SET dict_value = jsonb_set(dict_value::jsonb, '{cost_sources}',
                   (SELECT coalesce(jsonb_agg(v), '[]'::jsonb)
                      FROM jsonb_array_elements(dict_value::jsonb -> 'cost_sources') AS v
                     WHERE v <> to_jsonb('report_payable'::text)), true)::json
         WHERE dict_type = 'caliber' AND dict_key = 'profit'
           AND jsonb_typeof(dict_value::jsonb -> 'cost_sources') = 'array'
           AND (dict_value::jsonb -> 'cost_sources' ? 'report_payable')
    """)
