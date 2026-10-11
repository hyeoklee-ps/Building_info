"""주소만으로 건축물대장 층별 면적을 살펴보는 Streamlit 앱."""
import os
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
ST_API = 'https://apis.data.go.kr/1613000/BldRgstHubService'
JUSO_API = 'https://business.juso.go.kr/addrlink/addrLinkApi.do'
PYUNG = Decimal('3.305785')


def normalize_key(value):
    # 공공데이터 포털은 인코딩/디코딩 키 모두 제공. requests가 인코딩하므로 먼저 해제.
    from urllib.parse import unquote
    return unquote((value or '').strip())


def as_decimal(v):
    try:
        return Decimal(str(v).strip()) if str(v).strip() else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def items_from_response(data):
    response = data.get('response', {})
    header = response.get('header', {})
    code = str(header.get('resultCode', '00'))
    if code not in ('00', '0'):
        raise RuntimeError(f"건축물대장 API 오류 {code}: {header.get('resultMsg', '상세 없음')}")
    body = response.get('body', {})
    raw = body.get('items', {})
    if not raw or not isinstance(raw, dict):
        return [], int(body.get('totalCount') or 0)
    rows = raw.get('item', [])
    return rows if isinstance(rows, list) else [rows], int(body.get('totalCount') or 0)


def api_json(url, params):
    try:
        response = requests.get(url, params=params, timeout=20, headers={'User-Agent': 'BuildingAreaViewer/1.0'})
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f'외부 API 연결 실패: {exc}') from exc
    except ValueError as exc:
        raise RuntimeError('API가 JSON이 아닌 내용을 반환했습니다. 인증키 또는 서비스 주소를 확인해주세요.') from exc


@st.cache_data(ttl=3600, show_spinner=False)
def search_address(keyword, key):
    data = api_json(JUSO_API, {'confmKey': key, 'currentPage': 1, 'countPerPage': 30,
                               'keyword': keyword, 'resultType': 'json'})
    result = data.get('results', {})
    common = result.get('common', {})
    if str(common.get('errorCode', '0')) != '0':
        raise RuntimeError(f"주소 API 오류: {common.get('errorMessage', '승인키를 확인해주세요.')}")
    return result.get('juso') or []


@st.cache_data(ttl=3600, show_spinner=False)
def building_info(endpoint, code, key):
    params = {'serviceKey': normalize_key(key), 'sigunguCd': code['sigunguCd'],
              'bjdongCd': code['bjdongCd'], 'platGbCd': code['platGbCd'],
              'bun': code['bun'], 'ji': code['ji'], '_type': 'json',
              'numOfRows': 1000, 'pageNo': 1}
    first = api_json(f'{ST_API}/{endpoint}', params)
    found, total = items_from_response(first)
    page = 2
    while len(found) < total and page <= 30:
        params['pageNo'] = page
        rows, _ = items_from_response(api_json(f'{ST_API}/{endpoint}', params))
        if not rows:
            break
        found.extend(rows)
        page += 1
    if len(found) < total:
        st.warning('API 결과가 많아 일부만 조회되었습니다. 해당 건물의 기록을 추가 확인하세요.')
    return found


def code_from_address(juso):
    adm = str(juso.get('admCd', ''))
    if not re.fullmatch(r'\d{10}', adm):
        raise ValueError('주소의 행정구역코드를 확인할 수 없습니다.')
    return {'sigunguCd': adm[:5], 'bjdongCd': adm[5:],
            'platGbCd': '1' if str(juso.get('mtYn', '0')) == '1' else '0',
            'bun': str(juso.get('lnbrMnnm') or '0').zfill(4),
            'ji': str(juso.get('lnbrSlno') or '0').zfill(4)}


def floor_sort(row):
    value = row.get('flrNo') or '0'
    try:
        level = int(str(value))
    except ValueError:
        level = 0
    return (0 if str(row.get('flrGbCdNm', '')).find('지하') >= 0 else 1, level)


def group_floors(rows):
    grouped = defaultdict(lambda: {'area': Decimal(0), 'uses': set(), 'count': 0})
    for row in rows:
        # 같은 층의 용도별 기록은 합산. 부속동/별동은 분리.
        building = str(row.get('mgmBldrgstPk') or row.get('dongNm') or '건물')
        dong = str(row.get('dongNm') or '동 이름 없음')
        floor = str(row.get('flrGbCdNm') or '') + ' ' + str(row.get('flrNo') or '') + '층'
        k = (building, dong, floor)
        entry = grouped[k]
        entry['area'] += as_decimal(row.get('area'))
        entry['uses'].add(str(row.get('mainPurpsCdNm') or row.get('etcPurps') or '미기재'))
        entry['count'] += 1
    return [dict(건물=k[1], 층=k[2].strip(), **{'면적 (㎡)': float(v['area']), '면적 (평)': round(float(v['area'] / PYUNG), 1), '용도': ' · '.join(sorted(v['uses']))})
            for k, v in grouped.items()]


def area_summary(rows):
    # 집합건축물 전유·공용면적: 해당 층의 전용면적이라고 단정하지 않는다.
    grouped = defaultdict(lambda: {'exclusive': Decimal(0), 'common': Decimal(0)})
    for r in rows:
        key = (str(r.get('dongNm') or '동 미기재'), str(r.get('flrNo') or '-'))
        label = str(r.get('exposPubuseGbCdNm') or '')
        area = as_decimal(r.get('area'))
        if '전유' in label:
            grouped[key]['exclusive'] += area
        elif '공용' in label:
            grouped[key]['common'] += area
    return [dict(동=k[0], 층=k[1], **{'전유면적 합계 (㎡)': float(v['exclusive']), '공용면적 합계 (㎡)': float(v['common'])})
            for k, v in grouped.items() if v['exclusive'] or v['common']]



def field(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return value
    return None

def parking_details(row):
    fields = [('옥내 자주식','indrAutoUtcnt'),('옥외 자주식','oudrAutoUtcnt'),
              ('옥내 기계식','indrMechUtcnt'),('옥외 기계식','oudrMechUtcnt')]
    output = []
    for label, key in fields:
        value = field(row, key)
        try:
            count = int(float(value)) if value is not None else None
        except (ValueError, TypeError):
            count = None
        output.append({'구분':label,'대수':count})
    total = sum(x['대수'] for x in output if x['대수'] is not None)
    return output, total if any(x['대수'] is not None for x in output) else None

def display_rows(rows, fields):
    return [{name: str(field(row,*keys) or '-') for name,keys in fields.items()} for row in rows]

st.set_page_config(page_title='건물 층별 면적 조회', page_icon='🏢', layout='wide')
st.markdown('''<style>.block-container{max-width:1100px;padding-top:2rem} h1{letter-spacing:-1px}
[data-testid="stMetricValue"]{font-size:1.8rem}</style>''', unsafe_allow_html=True)
st.title('🏢 건물 층별 면적 조회')
st.caption('주소 검색 → 건물 선택 → 층별 면적(㎡·평) 조회 | 휴대폰 지원')

st.info('📱 휴대폰에서도 사용 가능합니다. 최초 1회 웹 배포가 필요하며, 이 화면에서 API 키를 입력할 수 있습니다.')
with st.expander('🔑 API 키 설정 (먼저 열어주세요)', expanded=True):
    juso_key = st.text_input('도로명주소 검색 API 승인키', value=os.getenv('JUSO_API_KEY', ''), type='password', key='juso_key')
    bld_key = st.text_input('건축HUB 건축물대장 API 일반 인증키', value=os.getenv('BUILDING_API_KEY', ''), type='password', key='bld_key')
    st.caption('API 키는 앱 서버로 전달되어 조회에 이용됩니다. 공유된 앱이나 신뢰할 수 없는 서버에는 키를 입력하지 마세요.')
    st.markdown('[주소 API 신청](https://business.juso.go.kr/addrlink/openApi/apiExprn.do)  ·  [건축HUB API 신청](https://www.data.go.kr/data/15134735/openapi.do)')
threshold = st.number_input('관심 최소 면적 (평)', value=100, min_value=0, step=10)

with st.form('address_search'):
    keyword = st.text_input('건물 주소', placeholder='예: 부산광역시 부산진구 중앙대로 708')
    submitted = st.form_submit_button('주소 검색', type='primary', use_container_width=True)
if submitted:
    st.session_state.pop('picked', None)
    if not juso_key:
        st.error('먼저 왼쪽에 도로명주소 API 승인키를 입력해주세요.')
    elif len(keyword.strip()) < 2:
        st.warning('검색할 주소를 입력해주세요.')
    else:
        try:
            with st.spinner('주소를 찾고 있습니다…'):
                st.session_state['addresses'] = search_address(keyword.strip(), juso_key)
        except Exception as exc:
            st.session_state['addresses'] = []
            st.error(str(exc))

addresses = st.session_state.get('addresses', [])
if addresses:
    st.subheader('1. 건물 선택')
    picked = st.selectbox('같은 도로명에 여러 건물이 있을 수 있습니다. 정확한 주소를 골라주세요.', range(len(addresses)),
        format_func=lambda i: f"{addresses[i].get('roadAddrPart1', '')} · {addresses[i].get('bdNm') or '건물명 없음'} | 지번 {addresses[i].get('jibunAddr', '')}")
    if st.button('층별 면적 조회', type='primary', use_container_width=True):
        if not bld_key:
            st.error('먼저 건축HUB API 인증키를 입력해주세요.')
        else:
            st.session_state['picked'] = addresses[picked]
            st.session_state.pop('results', None)
            try:
                with st.spinner('건축물대장 층별 정보를 조회하고 있습니다…'):
                    code = code_from_address(addresses[picked])
                    floors = building_info('getBrFlrOulnInfo', code, bld_key)
                    titles = building_info('getBrTitleInfo', code, bld_key)
                    try:
                        ownership = building_info('getBrExposPubuseAreaInfo', code, bld_key)
                    except Exception:
                        ownership = []
                    extra = {}
                    for name, endpoint in [
                        ('recap','getBrRecapTitleInfo'),
                        ('units','getBrExposInfo'),
                        ('zones','getBrJijiguInfo'),
                        ('prices','getBrHsprcInfo'),
                    ]:
                        try:
                            extra[name] = building_info(endpoint, code, bld_key)
                        except Exception as exc:
                            extra[name] = []
                            st.warning(f'{name} 조회 실패: {exc}')
                    st.session_state['results'] = {'floors': floors, 'titles': titles,
                        'ownership': ownership, 'code': code, **extra}
            except Exception as exc:
                st.error(str(exc))

results = st.session_state.get('results')
if results:
    st.divider()
    st.subheader('2. 층별 면적')
    st.caption(st.session_state['picked'].get('roadAddrPart1', ''))
    grouped = group_floors(results['floors'])
    if not grouped:
        st.warning('해당 지번에서 층별개요 자료가 조회되지 않았습니다. 건물 지번·부속지번 또는 대장 현황을 확인해주세요.')
    else:
        grouped = sorted(grouped, key=lambda r: (r['건물'], floor_sort({'flrGbCdNm': r['층'], 'flrNo': re.search(r'\d+', r['층']).group() if re.search(r'\d+', r['층']) else '0'})))
        biggest = max(x['면적 (평)'] for x in grouped)
        above = sum(x['면적 (평)'] >= threshold for x in grouped)
        a,b,c = st.columns(3)
        a.metric('조회된 층·동 조합', f'{len(grouped)}개')
        b.metric('최대 층별 면적', f'{biggest:,.1f}평')
        c.metric(f'{threshold}평 이상', f'{above}개')
        option = st.checkbox(f'{threshold}평 이상만 보기')
        shown = [x for x in grouped if x['면적 (평)'] >= threshold] if option else grouped
        st.dataframe(shown, use_container_width=True, hide_index=True, column_config={
            '면적 (㎡)': st.column_config.NumberColumn(format='%.2f'),
            '면적 (평)': st.column_config.NumberColumn(format='%.1f')})
        import pandas as pd
        st.download_button('CSV 저장', pd.DataFrame(shown).to_csv(index=False).encode('utf-8-sig'),
                           file_name='건물_층별면적.csv', mime='text/csv')
    st.subheader('3. 병원 개원 관련 건물 분석')
    for t in results.get('titles') or results.get('recap') or []:
        with st.container(border=True):
            st.markdown(f"**{field(t,'dongNm') or '건물'} · 건축물대장 표제부**")
            parking, total = parking_details(t)
            c1,c2,c3 = st.columns(3)
            c1.metric('대장상 주차대수', f'{total}대' if total is not None else '미기재')
            c2.metric('지상/지하 층수', f"{field(t,'grndFlrCnt') or '-'}/{field(t,'ugrndFlrCnt') or '-'}")
            raw_date = str(field(t,'useAprDay') or '')
            c3.metric('사용승인일', raw_date[:4]+'-'+raw_date[4:6]+'-'+raw_date[6:8] if len(raw_date)>=8 else '미기재')
            st.write(f"**주용도:** {field(t,'mainPurpsCdNm') or '미기재'} · **구조:** {field(t,'strctCdNm') or '미기재'}")
            st.write(f"**연면적:** {field(t,'totArea') or '미기재'}㎡ · **건축면적:** {field(t,'archArea') or '미기재'}㎡")
            st.dataframe(parking, use_container_width=True, hide_index=True)
    st.caption('주차대수는 대장상 설치대수이며, 방문객용 주차 가능 면수와 다릅니다. 여러 표제부 수치를 무조건 합산하지 마세요.')

    with st.expander('개별 호실 및 전유부'):
        rows = results.get('units',[])
        if rows:
            st.dataframe(display_rows(rows,{'동':('dongNm',),'층':('flrNo',),'호':('hoNm',),'용도':('mainPurpsCdNm','etcPurps'),'면적(㎡)':('area',)}),hide_index=True,use_container_width=True)
        else:
            st.info('전유부 데이터가 없습니다. 일반건축물일 수 있습니다.')

    with st.expander('지역지구구역'):
        rows = results.get('zones',[])
        if rows:
            st.dataframe(display_rows(rows,{'구분':('jijiguGbCdNm','jijiguGbCd'),'지역지구':('jijiguCdNm','etcJijigu')}),hide_index=True,use_container_width=True)
        else:
            st.info('지역지구구역 자료가 없습니다.')

    with st.expander('주택가격 (주거용 자료에 한함)'):
        rows = results.get('prices',[])
        if rows:
            st.dataframe(display_rows(rows,{'동':('dongNm',),'호':('hoNm',),'기준일':('stdDay','pblntfDay'),'가격 원자료':('hsprc','hsprcAmt','pblntfPc')}),hide_index=True,use_container_width=True)
            if st.checkbox('주택가격 원본 필드 보기'):
                st.json(rows)
        else:
            st.info('가격 정보가 없습니다. 상업용 건물은 해당 자료가 없을 수 있습니다.')
        st.caption('주택가격은 상가의 시세나 임대료를 의미하지 않습니다.')

    with st.expander('병원 개원 현장 체크리스트'):
        for i,item in enumerate([
            '실제 임대 가능 전용면적, 기둥과 공용부분 확인',
            '자주식 주차 배정 및 무료주차 가능 여부',
            '엘리베이터 및 환자·보호자 출입 동선',
            '의원 용도 적합성, 소방·피난·장애인 편의시설',
            '전력·급배수·수술실 설비 설치 가능성',
            '간판, 임대료·관리비, 명도·인테리어 공사 일정']):
            st.checkbox(item,key=f'check_{i}')
        st.caption('해당 체크리스트는 현장 실사와 관할 기관 확인이 필요합니다.')

    summary=area_summary(results['ownership'])
    if summary:
        with st.expander('전유·공용면적 원자료 합계 (집합건축물 참고용)'):
            st.dataframe(summary, hide_index=True, use_container_width=True)
            st.caption('호실별·동별 정보가 혼합될 수 있고 층별개요 면적과 직접 대응하지 않을 수 있습니다. 임대하려는 특정 호실의 전유부는 원본 대장과 대조하세요.')
    st.info('**주의:** 위 ‘층별 면적’은 건축물대장 층별개요 면적을 합산한 값이지, 임차 가능한 전용면적이 아닙니다. 여러 동/호실, 용도별 면적이 기록될 수 있습니다. 실제 계약 면적은 건축물대장 원본, 도면, 임대인 자료로 검증해야 합니다.')
else:
    st.info('왼쪽에 API 키 2개를 설정한 다음 주소를 검색하세요. 실제 데이터는 키 설정 후 표시됩니다.')
