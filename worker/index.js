// Cloudflare Worker - Building_info secure API relay.
// Secrets: KAKAO_REST_API_KEY, JUSO_API_KEY, BUILDING_API_KEY
// Optional var: ALLOWED_ORIGIN (e.g. https://hyeoklee-ps.github.io)
const HUB='https://apis.data.go.kr/1613000/BldRgstHubService';
const JUSO='https://business.juso.go.kr/addrlink/addrLinkApi.do';
const KAKAO='https://dapi.kakao.com/v2/local';
const endpoints={floors:'getBrFlrOulnInfo',titles:'getBrTitleInfo',
 ownership:'getBrExposPubuseAreaInfo',recap:'getBrRecapTitleInfo',
 units:'getBrExposInfo',zones:'getBrJijiguInfo',prices:'getBrHsprcInfo'};
function cors(origin,env){
 const allowed=env.ALLOWED_ORIGIN||'https://hyeoklee-ps.github.io';
 return {'Access-Control-Allow-Origin':origin===allowed?origin:allowed,
 'Access-Control-Allow-Methods':'POST,OPTIONS','Access-Control-Allow-Headers':'Content-Type',
 'Vary':'Origin','Cache-Control':'no-store'};
}
function fail(message,status=400,origin='',env={}){return new Response(JSON.stringify({error:message}),{status,headers:{'Content-Type':'application/json',...cors(origin,env)}})}
async function fetchJSON(url,headers={},timeout=11000){
 const controller=new AbortController();const t=setTimeout(()=>controller.abort(),timeout);
 try{const r=await fetch(url,{headers,signal:controller.signal});
 if(!r.ok)throw Error('upstream HTTP '+r.status);
 return await r.json()}finally{clearTimeout(t)}
}
const codePattern=/^\d{10}$/;
function mapAddress(doc){
 const a=doc.address||{},r=doc.road_address||{},main=String(a.main_address_no||'');
 const sub=String(a.sub_address_no||'0'),bc=String(a.b_code||'');
 if(!codePattern.test(bc)||!/^\d{1,4}$/.test(main)||!/^\d{1,4}$/.test(sub))return null;
 return {code:{sigunguCd:bc.slice(0,5),bjdongCd:bc.slice(5),
 platGbCd:a.mountain_yn==='Y'?'1':'0',bun:main.padStart(4,'0'),ji:sub.padStart(4,'0')},
 roadAddrPart1:r.address_name||'',jibunAddr:a.address_name||'',bdNm:r.building_name||''};
}
function mapJuso(j){
 const bc=String(j.admCd||'');if(!codePattern.test(bc))return null;
 const main=String(j.lnbrMnnm||'0'),sub=String(j.lnbrSlno||'0');
 if(!/^\d{1,4}$/.test(main)||!/^\d{1,4}$/.test(sub))return null;
 return {code:{sigunguCd:bc.slice(0,5),bjdongCd:bc.slice(5),
 platGbCd:String(j.mtYn)==='1'?'1':'0',bun:main.padStart(4,'0'),ji:sub.padStart(4,'0')},
 roadAddrPart1:j.roadAddrPart1||'',jibunAddr:j.jibunAddr||'',bdNm:j.bdNm||''};
}
function unique(arr){const m=new Map();for(const a of arr.filter(Boolean)){const k=Object.values(a.code).join(':');if(!m.has(k))m.set(k,a)}return [...m.values()]}
async function kakaoAddress(query,env){
 const url=new URL(KAKAO+'/search/address.json');url.searchParams.set('query',query);
 url.searchParams.set('analyze_type','exact');url.searchParams.set('size','10');
 const data=await fetchJSON(url,{'Authorization':'KakaoAK '+env.KAKAO_REST_API_KEY});
 return unique((data.documents||[]).map(mapAddress));
}
async function jusoAddress(query,env){
 if(!env.JUSO_API_KEY)return [];
 const url=new URL(JUSO);url.searchParams.set('confmKey',env.JUSO_API_KEY);
 url.searchParams.set('keyword',query);url.searchParams.set('currentPage','1');
 url.searchParams.set('countPerPage','30');url.searchParams.set('resultType','json');
 const data=await fetchJSON(url,{},8000);
 if(String(data?.results?.common?.errorCode||'0')!=='0')throw Error('juso search failed');
 return unique((data?.results?.juso||[]).map(mapJuso));
}
async function getAddress(query,env){
 try{const found=await kakaoAddress(query,env);if(found.length)return found}catch(_){}
 try{return await jusoAddress(query,env)}catch(_){return []}
}
function parseItems(data){
 if(String(data?.response?.header?.resultCode||'00')!=='00'&&String(data?.response?.header?.resultCode||'0')!=='0')return [];
 const body=data?.response?.body||{},raw=body.items?.item||[];
 return {rows:Array.isArray(raw)?raw:[raw],total:Number(body.totalCount||0)};
}
async function hub(endpoint,code,env){
 const key=decodeURIComponent(env.BUILDING_API_KEY);
 let output=[],page=1,total=0;
 do{
  const url=new URL(HUB+'/'+endpoint);url.searchParams.set('serviceKey',key);
  for(const k of ['sigunguCd','bjdongCd','platGbCd','bun','ji'])url.searchParams.set(k,code[k]);
  url.searchParams.set('_type','json');url.searchParams.set('pageNo',String(page));
  url.searchParams.set('numOfRows','1000');
  const data=await fetchJSON(url,{},14000);
  const parsed=parseItems(data);if(!parsed||!parsed.rows?.length)break;
  output.push(...parsed.rows);total=parsed.total;page++;
 }while(output.length<total&&page<=10);
 return output;
}
export default {async fetch(request,env){
 const origin=request.headers.get('Origin')||'';
 const allowed=env.ALLOWED_ORIGIN||'https://hyeoklee-ps.github.io';
 if(request.method==='OPTIONS')return new Response('',{status:204,headers:cors(origin,env)});
 if(request.method!=='POST')return fail('POST 요청만 지원합니다.',405,origin,env);
 if(origin!==allowed)return fail('허용되지 않은 웹사이트입니다.',403,origin,env);
 if(!env.KAKAO_REST_API_KEY||!env.BUILDING_API_KEY)return fail('서버 API 키 설정이 필요합니다.',503,origin,env);
 try{
  const path=new URL(request.url).pathname;
  const body=await request.json();
  let response;
  if(path==='/place'){
   const query=String(body.query||'').trim().slice(0,100);if(query.length<2)return fail('검색어를 입력하세요.',400,origin,env);
   const url=new URL(KAKAO+'/search/keyword.json');url.searchParams.set('query',query);url.searchParams.set('size','15');
   const data=await fetchJSON(url,{'Authorization':'KakaoAK '+env.KAKAO_REST_API_KEY});
   response={places:data.documents||[]};
  }else if(path==='/address'){
   const q=String(body.query||'').trim().slice(0,160);if(q.length<3)return fail('주소를 입력하세요.',400,origin,env);
   response={addresses:await getAddress(q,env)};
  }else if(path==='/resolve'){
   const place=body.place||{},queries=[place.address_name,place.road_address_name].filter(Boolean);
   let addresses=[];for(const q of queries){addresses=await getAddress(String(q).slice(0,160),env);if(addresses.length)break}
   response={addresses};
  }else if(path==='/building'){
   const code=body.code||{};
   if(!/^\d{5}$/.test(code.sigunguCd||'')||!/^\d{5}$/.test(code.bjdongCd||'')||
      !/^[01]$/.test(code.platGbCd||'')||!/^[0-9]{4}$/.test(code.bun||'')||
      !/^[0-9]{4}$/.test(code.ji||''))return fail('올바르지 않은 건축물 지번 코드입니다.',400,origin,env);
   const floors=await hub(endpoints.floors,code,env);
   const titles=await hub(endpoints.titles,code,env);
   // Optional sections are queried in parallel; noncritical failure is tolerated.
   const optional=await Promise.allSettled(
    ['ownership','recap','units','zones','prices'].map(k=>hub(endpoints[k],code,env)));
   response={floors,titles};
   ['ownership','recap','units','zones','prices'].forEach((k,i)=>{
     response[k]=optional[i].status==='fulfilled'?optional[i].value:[]});
  }else{return fail('지원하지 않는 경로입니다.',404,origin,env)}
  return new Response(JSON.stringify(response),{headers:{'Content-Type':'application/json; charset=utf-8',...cors(origin,env)}});
 }catch(e){return fail('외부 데이터 요청 실패 또는 응답 지연. 잠시 뒤 재시도해 주세요.',502,origin,env)}
}};
