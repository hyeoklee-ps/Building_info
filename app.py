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
def search_places(keyword, key):
    response = api_json_with_headers(
        'https://dapi.kakao.com/v2/local/search/keyword.json',
        {'query': keyword, 'size': 15},
        {'Authorization': 'KakaoAK ' + key.strip()})
    return response.get('documents') or []


def api_json_with_headers(url, params, headers):
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else '?'
        raise RuntimeError(f'장소 검색 API 오류 (HTTP {status}): 카카오 REST API 키와 로컬 API 활성화 여부를 확인하세요.') from exc
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f'장소 검색 연결 실패: {exc}') from exc


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

st.set_page_config(page_title='건축물대장 정보', page_icon='🏢', layout='wide')
st.markdown('''<style>.block-container{max-width:1100px;padding-top:2rem} h1{letter-spacing:-1px}
[data-testid="stMetricValue"]{font-size:1.8rem}</style>''', unsafe_allow_html=True)
st.title('건축물대장 정보')
st.caption('주소 검색 → 건물 선택 → 층별 면적·주차·건축물대장 정보 조회')

def saved_api_key(name):
    try:
        return str(st.secrets.get(name, '')).strip() or os.getenv(name, '')
    except Exception:
        return os.getenv(name, '')

juso_key = saved_api_key('JUSO_API_KEY')
bld_key = saved_api_key('BUILDING_API_KEY')
if juso_key and bld_key:
    st.caption('🔒 API 인증키: Streamlit 비공개 설정에서 불러왔습니다.')
else:
    with st.expander('🔑 API 키 설정 (Streamlit Cloud Secrets에 등록하면 자동 입력됩니다)', expanded=True):
        if not juso_key:
            juso_key = st.text_input('도로명주소 검색 API 승인키', type='password', key='juso_key')
        if not bld_key:
            bld_key = st.text_input('건축HUB 건축물대장 API 일반 인증키', type='password', key='bld_key')
        st.caption('일시 입력한 키는 앱을 다시 열면 유지되지 않습니다. 영구 설정은 Streamlit Cloud 앱 Settings → Secrets에서 등록하세요.')
        st.markdown('[주소 API](https://business.juso.go.kr/) · [건축HUB API](https://www.data.go.kr/data/15134735/openapi.do)')

kakao_key = saved_api_key('KAKAO_REST_API_KEY')
if not kakao_key:
    with st.expander('상호명 검색 활성화 (카카오 REST API 키)', expanded=False):
        kakao_key = st.text_input('카카오 REST API 키', type='password', key='kakao_key')
        st.caption('영구 저장: Streamlit Cloud → Settings → Secrets에 KAKAO_REST_API_KEY를 등록하세요.')

def place_floor(place):
    """카카오 장소검색 기본 응답에는 층수 전용 필드가 없다.
    주소나 상호에 층수가 명시된 경우에만 표시하며, 추정하지 않는다."""
    texts = [str(place.get(k) or '') for k in
             ('road_address_name', 'address_name', 'place_name')]
    for source in texts:
        match = re.search(r'(?<!\d)(지하\s*\d+|B\s*\d+|\d+)\s*층(?!\d)', source, re.I)
        if match:
            return re.sub(r'\s+', '', match.group(0))
    return '층수 미확인'


def place_label(place):
    addr = place.get('road_address_name') or place.get('address_name') or ''
    return f"{place.get('place_name','')} · {addr} · {place_floor(place)}"


def resolve_place(place):
    if not juso_key:
        st.error('도로명주소 API 키를 먼저 설정해주세요.')
        return
    target = place.get('road_address_name') or place.get('address_name') or ''
    if not target:
        st.warning('해당 장소에는 주소가 없습니다. 주소 검색을 이용하세요.')
        return
    try:
        with st.spinner('장소 주소를 건축물 주소로 변환하고 있습니다…'):
            found = search_address(target, juso_key)
            if not found and place.get('address_name') and place['address_name'] != target:
                found = search_address(place['address_name'], juso_key)
        st.session_state['addresses'] = found
        if not found:
            st.warning('건축물 주소로 연결되지 않았습니다. 주소 검색을 이용하세요.')
    except Exception as exc:
        st.error(str(exc))


def fetch_building(address):
    if not bld_key:
        st.error('건축HUB API 인증키를 먼저 설정해주세요.')
        return
    st.session_state['picked'] = address
    st.session_state.pop('results', None)
    try:
        with st.spinner('건축물대장 정보를 조회하고 있습니다…'):
            code = code_from_address(address)
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
            st.session_state['results'] = {
                'floors': floors, 'titles': titles, 'ownership': ownership,
                'code': code, **extra
            }
    except Exception as exc:
        st.error(str(exc))


search_mode = st.radio('검색 방식', ['상호명 검색', '주소 검색'],
                       horizontal=True, index=0 if kakao_key else 1)
with st.form('building_search'):
    keyword = st.text_input(
        '상호명 또는 주소',
        placeholder='예: 더페이스성형외과' if search_mode == '상호명 검색'
        else '예: 부산진구 부전로66번길 22')
    submitted = st.form_submit_button('검색', type='primary', use_container_width=True)

if submitted:
    for state_key in ('picked', 'addresses', 'places', 'results'):
        st.session_state.pop(state_key, None)
    if len(keyword.strip()) < 2:
        st.warning('검색어를 두 글자 이상 입력해주세요.')
    elif search_mode == '상호명 검색':
        if not kakao_key:
            st.error('상호명 검색에는 카카오 REST API 키가 필요합니다.')
        else:
            try:
                with st.spinner('상호명을 검색하고 있습니다…'):
                    st.session_state['places'] = search_places(keyword.strip(), kakao_key)
                if not st.session_state['places']:
                    st.warning('장소 검색 결과가 없습니다. 지역명을 추가하거나 주소 검색을 이용하세요.')
            except Exception as exc:
                st.error(str(exc))
    elif not juso_key:
        st.error('도로명주소 API 키를 먼저 설정해주세요.')
    else:
        try:
            with st.spinner('주소를 찾고 있습니다…'):
                st.session_state['addresses'] = search_address(keyword.strip(), juso_key)
        except Exception as exc:
            st.error(str(exc))

places = st.session_state.get('places') or []
if len(places) == 1 and submitted:
    # 상호명 한 건이면 추가 버튼 없이 바로 주소를 변환한다.
    resolve_place(places[0])
elif len(places) > 1:
    st.subheader('1. 상호명 검색 결과')
    idx = st.selectbox('동일·유사 상호가 여러 개 있습니다. 정확한 지점을 선택하세요.',
                       range(len(places)), format_func=lambda i: place_label(places[i]))
    chosen = places[idx]
    st.caption(f"선택 장소: {chosen.get('place_name','')} | "
               f"{chosen.get('category_name','')} | {place_floor(chosen)} | "
               f"지번: {chosen.get('address_name','')}")
    if st.button('이 장소 선택', type='primary', use_container_width=True):
        st.session_state.pop('results', None)
        st.session_state.pop('addresses', None)
        resolve_place(chosen)
elif len(places) == 1:
    st.caption(f"검색된 장소: {place_label(places[0])}")

addresses = st.session_state.get('addresses') or []
if len(addresses) == 1:
    # 주소 검색이든 장소 검색이든 단일 건물이면 바로 조회한다.
    address = addresses[0]
    st.caption(f"조회 건물: {address.get('roadAddrPart1','')} · "
               f"지번 {address.get('jibunAddr','')}")
    if not st.session_state.get('results'):
        fetch_building(address)
elif len(addresses) > 1:
    st.subheader('2. 건물 주소 선택')
    picked = st.selectbox('검색된 건물이 여러 개입니다. 주소를 확인하세요.',
        range(len(addresses)),
        format_func=lambda i: f"{addresses[i].get('roadAddrPart1', '')} · "
        f"{addresses[i].get('bdNm') or '건물명 없음'} | "
        f"지번 {addresses[i].get('jibunAddr', '')}")
    if st.button('건축물대장 조회', type='primary', use_container_width=True):
        fetch_building(addresses[picked])

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
        a,b = st.columns(2)
        a.metric('조회된 층·동 조합', f'{len(grouped)}개')
        b.metric('최대 층별 면적', f'{biggest:,.1f}평')
        shown = [{k: v for k, v in row.items() if k != '건물'} for row in grouped]
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
    st.info('주소를 입력해 건축물대장 정보를 조회하세요. API 키가 등록되지 않았다면 상단에서 설정해 주세요.')
