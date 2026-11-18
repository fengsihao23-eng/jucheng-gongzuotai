"""Current-value business detail, verified links, permissions and read-only behavior."""
import copy
from dataclasses import replace
from decimal import Decimal
import json

import pytest
from sqlalchemy import func, select

from app.models import BizContract, BizPayment, ContractWorkbookLink, SysUser
from app.services import business_workbook as source, performance_details as details, performance_workbook
from tests.conftest import data
from tests.test_business_workbook import source_only

BASE='/api/v1/business-workbook/contracts'


def bind_existing(db, wb, principal):
    record=source.module_sheets(wb,'contracts')[0].source['rows'][-1]['record']
    contract=db.scalar(select(BizContract).where(BizContract.owner_id != principal.id, BizContract.is_deleted.is_(False)))
    link=ContractWorkbookLink(workbook_id=wb.id,kind='contract',target_id=contract.id,sheet_id='sales-sheet',row_number=3,
                             keys=record['keys'],baseline={'name':record['name'],'customer_name':record['customer_name']},present=True)
    db.add(link)
    db.commit()
    return contract,link


def test_clickable_business_details_preserve_source_and_stored_data(db,source_only,client,login,request):
    wb,p=source_only
    contract,link=bind_existing(db,wb,p)
    previous_name,previous_amount=contract.name,contract.amount
    def restore():
        contract.name,contract.amount=previous_name,previous_amount
        db.commit()
    request.addfinalizer(restore)
    contract.amount=Decimal('99999.00')
    contract.name='旧台账名称不同'
    db.commit()
    source_before=json.dumps(wb.sheets,sort_keys=True)
    core_before=(contract.name,contract.amount,contract.customer_name,contract.sign_date)
    rows=performance_workbook.page(wb,p)
    response=data(client.get(BASE,headers=login('admin')))
    assert response['summary']==rows['summary']
    item=response['items'][0]
    assert item['detail_path']=='/contracts/items/sales-sheet/3'
    assert item['contract_no']==contract.contract_no
    assert item['amount']=='100.00' and item['received']=='30.00' and item['outstanding']=='65.00'
    result=data(client.get(BASE+'/items/sales-sheet/3?revision=1',headers=login('admin')))
    assert result['contract']['name']=='唯一原表合同'
    assert result['contract']['amount']=='100.00'
    assert result['contract']['received']=='30.00' and result['contract']['outstanding']=='65.00'
    assert result['contract']['received_rate']==0.3
    assert 'rows' not in result and 'cells' not in result
    assert result['contract']['business_line_name'] is None and result['contract']['status_name'] is None
    db.expire_all()
    assert (contract.name,contract.amount,contract.customer_name,contract.sign_date)==core_before
    assert json.dumps(wb.sheets,sort_keys=True)==source_before


def test_unlinked_row_also_has_business_detail_and_attachments(db,source_only,client,login):
    wb,p=source_only
    before=db.scalar(select(func.count()).select_from(BizContract))
    result=data(client.get(BASE+'/items/sales-sheet/3',headers=login('admin')))
    assert result['contract']['linked_contract_id'] is None
    assert result['contract']['amount']=='100.00'
    assert result['attachments'][0]['filename']=='合同原件'
    assert not result['related']['plans'] and not result['related']['payments']
    assert db.scalar(select(func.count()).select_from(BizContract))==before


def test_link_follows_identity_when_source_row_moves_without_writing_link(db,source_only,client,login):
    wb,p=source_only
    contract,link=bind_existing(db,wb,p)
    changed=copy.deepcopy(wb.sheets)
    changed[0]['rows'][-1]['row']=17
    wb.sheets,wb.revision=changed,'2'
    db.commit()
    assert client.get(BASE+'/items/sales-sheet/17?revision=1',headers=login('admin')).status_code==409
    assert client.get(BASE+'/items/sales-sheet/3?revision=2',headers=login('admin')).status_code==404
    result=data(client.get(BASE+'/items/sales-sheet/17?revision=2',headers=login('admin')))
    assert result['contract']['linked_contract_id']==contract.id
    db.refresh(link)
    assert link.row_number==3


def test_ambiguous_identity_does_not_attach_another_contract_files(db,source_only,client,login):
    wb,p=source_only
    contract,link=bind_existing(db,wb,p)
    other=db.scalar(select(BizContract).where(BizContract.id != contract.id, BizContract.is_deleted.is_(False)))
    db.add(ContractWorkbookLink(workbook_id=wb.id,kind='contract',target_id=other.id,sheet_id=link.sheet_id,row_number=4,
                               keys=copy.deepcopy(link.keys),baseline=copy.deepcopy(link.baseline),present=True))
    db.commit()
    result=data(client.get(BASE+'/items/sales-sheet/3',headers=login('admin')))
    assert result['contract']['linked_contract_id'] is None
    assert result['related']['files']==[] and result['related']['project'] is None
    assert result['contract']['amount']=='100.00'


def test_detail_preserves_source_permission_and_linked_contract_scope(db,source_only,client,login):
    wb,p=source_only
    contract,link=bind_existing(db,wb,p)
    assert client.get(BASE+'/items/sales-sheet/3',headers=login('zhangzhenhua')).status_code==403
    limited=replace(p,data_scope='self')
    result=details.detail(db,limited,'sales-sheet',3)
    assert result['contract']['linked_contract_id'] is None
    assert not result['related']['files'] and result['related']['project'] is None
    assert result['contract']['name']=='唯一原表合同'
    assert client.get(BASE+'/items/foreign-sheet/3',headers=login('admin')).status_code==404
    assert client.get(BASE+'/items/sales-sheet/1',headers=login('admin')).status_code==404


def test_supplementary_files_and_business_notes_stay_with_parent(db,source_only,client,login):
    wb,p=source_only
    changed=copy.deepcopy(wb.sheets)
    changed[0]['merges']=['B3:B4','F3:F4']
    child=[None]*16
    child[14]='补充条款说明'
    child[15]={'rich_string':[{'link':'https://drive.weixin.qq.com/s?k=supplement','link_text':'补充协议.pdf'}]}
    changed[0]['rows'].append({'row':4,'cells':child})
    wb.sheets=changed
    db.commit()
    result=data(client.get(BASE+'/items/sales-sheet/3',headers=login('admin')))
    assert {f['filename'] for f in result['attachments']}=={'合同原件','补充协议.pdf'}
    assert result['notes']==['补充条款说明']
    assert result['contract']['amount']=='100.00'
    assert data(client.get(BASE,headers=login('admin')))['total']==1
