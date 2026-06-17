// driver helpers — hoisted so available on first render
// Supabase client — driver profiles stored in cloud DB
// SUPA_URL / SUPA_KEY are injected by index.html (build_html.py) before this loads
const SUPA_URL=window.SUPA_URL, SUPA_KEY=window.SUPA_KEY;
const _sb=supabase.createClient(SUPA_URL,SUPA_KEY);
var _hashType=(function(){var m=/[#&]type=([a-z_]+)/.exec(location.hash||'');return m?m[1]:'';})();
// Supabase bounces FAILED sign-in links (expired/already-used invite, recovery,
// magic-link, or OAuth errors) back here as #error=...&error_code=...&error_description=...
// (or as ?query params in the PKCE flow). Capture it so the login screen can explain
// the dead end + offer a fresh link, instead of dumping the user on a raw error URL.
var _hashErr=(function(){
  var s=(location.hash||'')+'&'+(location.search||'');
  if(s.indexOf('error')<0) return null;
  function g(k){var m=new RegExp('[#&?]'+k+'=([^&]*)').exec(s);return m?decodeURIComponent((m[1]||'').replace(/\+/g,' ')):'';}
  var code=g('error_code'), e=g('error'); if(!code&&!e) return null;
  return {code:code,error:e,desc:g('error_description')};
})();
function _clearAuthHash(){
  try{
    var p=new URLSearchParams(location.search||'');
    ['error','error_code','error_description'].forEach(function(k){ p.delete(k); });
    var q=p.toString();
    history.replaceState(null,'',location.pathname+(q?'?'+q:'')); // drop the #hash + error params, but keep any ?code/?state the SDK still needs
  }catch(e){}
  _hashErr=null; // consume once so the boot guard doesn't persist for the whole page session
}
var _linkExpiredShown=false; // true while the expired-link screen is showing with no session
var currentUser=null, currentRole='user';
let _drCache={};
function loadDrivers(){ return _drCache; }
function saveDrivers(data){ _drCache=data; }
async function initDrivers(){
  const {data}=await _sb.from('drivers').select('*');
  _drCache={};
  if(data) data.forEach(function(r){ _drCache[r.plate]={first:r.first_name||'',last:r.last_initial||'',age:r.age||'',sex:r.sex||'',email:r.email||''}; });
}
const GOLD='#EF9F27';
function sc(s){if(s>=90)return'var(--success)';if(s>=70)return'var(--info)';if(s>=50)return'var(--warning)';return'var(--danger)';}
function fmt1(x){return (x==null||isNaN(x))?'—':(+x).toFixed(1);} // scores shown to 1 decimal
function starN(s){return s>=100?5:s>=90?4:s>=80?3:s>=70?2:1;} // whole stars; 5 only at 100, so 99=4
function starsHtml(s){var n=starN(s);return'<span style="color:var(--gold);">'+'★'.repeat(n)+'</span>'+(n<5?'<span style="color:var(--text3);">'+'★'.repeat(5-n)+'</span>':'');}
function stars(s){return starsHtml(s);}
function starsN(s){return starsHtml(s);}
function tot(t){if(t.lc)return Math.round(0.364*t.brk+0.364*t.crn+0.273*t.acc);return Math.round(0.45*t.spd+0.20*t.brk+0.20*t.crn+0.15*t.acc);}
function tripSize(km){if(km<10)return'short';if(km>=25)return'long';return'standard';}
function trendIcon(t,sz){sz=sz||13;if(t==='improving')return'<i class="ti ti-trending-up" style="font-size:'+sz+'px;color:var(--success);" title="Improving"></i>';if(t==='declining')return'<i class="ti ti-trending-down" style="font-size:'+sz+'px;color:var(--danger);" title="Declining"></i>';if(t==='stable')return'<i class="ti ti-minus" style="font-size:'+sz+'px;color:var(--text3);" title="Stable"></i>';return'<span style="color:var(--text3);font-size:10px;">—</span>';}
function cv(val){return val===null||val===undefined?'<span style="color:var(--text3);">—</span>':'<span style="color:'+sc(val)+';">'+val+'</span>';}

let vehicles=[];
let incData=[];
let weeksData=[];

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(tab){
  document.getElementById('view-dash').style.display    = tab==='dash'?'':'none';
  document.getElementById('view-lb').style.display      = tab==='lb'?'':'none';
  document.getElementById('view-drivers').style.display = tab==='drivers'?'':'none';
  document.getElementById('view-faq').style.display     = tab==='faq'?'':'none';
  document.getElementById('view-admin').style.display   = tab==='admin'?'':'none';
  document.getElementById('tab-dash').classList.toggle('active',    tab==='dash');
  document.getElementById('tab-lb').classList.toggle('active',      tab==='lb');
  document.getElementById('tab-drivers').classList.toggle('active', tab==='drivers');
  document.getElementById('tab-faq').classList.toggle('active',     tab==='faq');
  document.getElementById('tab-admin').classList.toggle('active',   tab==='admin');
  if(tab==='lb') renderLeaderboard();
  if(tab==='drivers') renderDrivers();
  if(tab==='dash') renderRanking();
  if(tab==='admin') loadAdminUsers();
  if(tab==='faq') setupFaqAccordion();
}

// FAQ: collapse each Q&A into a click-to-expand item (first one left open).
// Progressive enhancement over the existing cards, so the answers still render
// even if this never runs.
function setupFaqAccordion(){
  var c=document.getElementById('faq-acc'); if(!c||c._faqDone) return; c._faqDone=true;
  var n=0;
  c.querySelectorAll(':scope > div').forEach(function(card){
    var divs=card.querySelectorAll(':scope > div');
    if(divs.length<2) return; // skip the intro callout (no inner question/answer divs)
    var q=divs[0], a=divs[1];
    card.classList.add('faq-item'); q.classList.add('faq-q'); a.classList.add('faq-a');
    var chev=document.createElement('span'); chev.className='faq-chev'; chev.textContent='▾';
    q.appendChild(chev);
    if(n>2) card.classList.add('faq-collapsed'); // first 3 open on load
    q.addEventListener('click',function(){ card.classList.toggle('faq-collapsed'); });
    n++;
  });
}

// ── Dashboard ─────────────────────────────────────────────────────────────
let sel=null;
function renderRanking(){
  const el=document.getElementById('ranking');el.innerHTML='';
  const M=['🥇','🥈','🥉'];
  vehicles.forEach(function(v,i){
    const row=document.createElement('div');
    row.className='rank-row'+(sel===v.plate?' active':'');
    const badge=i<3?'<span style="font-size:15px;">'+M[i]+'</span>':'<span style="font-size:12px;font-weight:600;color:var(--text2);">#'+(i+1)+'</span>';
    const ti=v.trend==='improving'
      ?'<i class="ti ti-trending-up" style="font-size:13px;color:var(--success);" title="Improving"></i>'
      :v.trend==='declining'
      ?'<i class="ti ti-trending-down" style="font-size:13px;color:var(--danger);" title="Declining"></i>'
      :v.trend==='stable'
      ?'<i class="ti ti-minus" style="font-size:13px;color:var(--text3);" title="Stable"></i>'
      :'';
    var drD=loadDrivers()[v.plate]||{};
    var dName=esc(((drD.first||'').trim()+' '+((drD.last||'').trim()?(drD.last||'').trim().toUpperCase()+'.':'')).trim());
    row.innerHTML=badge+
      '<div style="flex:1;min-width:0;">'+
        '<div style="display:flex;align-items:center;gap:5px;margin-bottom:1px;">'+
          (dName
            ? '<span style="font-size:12px;font-weight:600;">'+dName+'</span>'
            : '<span style="font-size:11px;font-weight:500;">'+v.plate+'</span>')+
          (v.low_cov?'<span style="font-size:9px;color:var(--warning);">low cov</span>':'')+
        '</div>'+
        (dName?'<div style="font-size:11px;color:var(--text2);margin-bottom:2px;">'+v.plate+' &middot; '+v.make+'</div>':
                '<div style="font-size:11px;color:var(--text2);margin-bottom:2px;">'+v.make+'</div>')+
        '<div class="bwrap"><div class="bfill" style="width:'+v.avg+'%;background:'+sc(v.avg)+';"></div></div>'+
      '</div>'+
      '<div style="display:flex;flex-direction:column;align-items:flex-end;gap:3px;min-width:54px;">'+
        '<div style="font-size:14px;font-weight:500;color:'+sc(v.avg)+';">'+fmt1(v.avg)+'</div>'+
        ti+
      '</div>';
    row.onclick=function(){selectV(v.plate);};
    el.appendChild(row);
  });
}

function selectV(plate){
  sel=plate;renderRanking();
  const v=vehicles.find(function(x){return x.plate===plate;});
  var _drD=loadDrivers()[plate]||{};
  var drName=esc(((_drD.first||'').trim()+' '+((_drD.last||'').trim()?(_drD.last||'').trim().toUpperCase()+'.':'')).trim());
  function ageTag(a){
    var m={'Under 25':['#c084fc','rgba(139,92,246,0.18)'],'25–34':['var(--info)','var(--info-bg)'],'35–44':['#5eead4','rgba(20,184,166,0.15)'],'45–54':['var(--warning)','var(--warning-bg)'],'55–64':['var(--success)','var(--success-bg)'],'65+':['#f87171','var(--danger-bg)']};
    var c=m[a];
    return a&&c?'<span style="font-size:9px;font-weight:600;padding:1px 6px;border-radius:3px;background:'+c[1]+';color:'+c[0]+';margin-left:7px;vertical-align:3px;">'+a+'</span>':'';
  }
  document.getElementById('trip-label').style.display='none'; // title now lives inside the summary card
  const el=document.getElementById('trips');
  const sumEl=document.getElementById('vehicle-summary');
  const compNames={spd:'Speeding',brk:'Braking',acc:'Acceleration',crn:'Cornering'};
  const coachTips={
    spd:'Keep to the posted speed limit on every route. Even a few minutes of speeding can drag your score down. Try using cruise control on motorways — it makes a noticeable difference within a week.',
    brk:'Leave a little more space between you and the car ahead. You will naturally brake earlier and smoother, and your braking score usually improves within a week once the habit clicks.',
    acc:'Press the accelerator a little more gently when pulling away from lights and roundabouts. This is one of the easiest things to change and most drivers see results within just a few days.',
    crn:'Ease off before corners rather than mid-turn. On routes you drive regularly, try slowing a little earlier going into bends — this tends to improve gradually over a couple of weeks.'
  };
  const nextTarget=v.next_target||null;
  const ptsNeeded=nextTarget?Math.round((nextTarget-v.avg)*10)/10:0;
  const predText=nextTarget
    ?'<div style="margin-top:10px;background:var(--bg3);border-radius:8px;padding:10px 12px;">'
      +'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;">'
        +'<span style="font-size:11px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.05em;">Next level</span>'
        +'<span style="font-size:12px;font-weight:500;color:var(--gold);">'+stars(nextTarget)+' '+nextTarget+'/100</span>'
        +'<span style="font-size:11px;color:var(--text3);">&mdash; '+ptsNeeded+' point'+(ptsNeeded!==1?'s':'')+' away</span>'
      +'</div>'
      +(v.pred_comp?'<p style="font-size:12px;color:var(--text2);line-height:1.6;"><strong>Focus: '+compNames[v.pred_comp]+'.</strong> '+coachTips[v.pred_comp]+'</p>':'')
    +'</div>'
    :'<div style="margin-top:8px;font-size:11px;color:var(--success);">&#10003; Top tier — keep this standard and your score will speak for itself.</div>';
  const trendBadge=v.trend==='improving'
    ?'<span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;color:var(--success);background:var(--success-bg);padding:2px 8px;border-radius:20px;margin-left:8px;"><i class="ti ti-trending-up" style="font-size:11px;"></i> Improving</span>'
    :v.trend==='declining'
    ?'<span style="display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;color:#fff;background:var(--danger);padding:2px 8px;border-radius:20px;margin-left:8px;"><i class="ti ti-trending-down" style="font-size:11px;"></i> Declining</span>'
    :'';
  // Per-driver risk badge (same bands as the fleet breakdown)
  var _rcm={Low:'var(--success)',Moderate:'var(--warning)',High:'var(--danger)'};
  function _f1(x){return (+x).toFixed(1);}
  var vRisk=(v.avg>=_riskBands.low)?'Low':((v.avg<_riskBands.high)?'High':'Moderate');
  var vRcol=_rcm[vRisk];
  var _vb={High:'&lt;'+_f1(_riskBands.high),Moderate:_f1(_riskBands.high)+'–'+_f1(_riskBands.low),Low:'&ge;'+_f1(_riskBands.low)};
  var riskBlock='<div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex-shrink:0;">'
    +'<span style="display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:600;letter-spacing:.5px;text-transform:uppercase;color:'+vRcol+';border:1px solid '+vRcol+';border-radius:11px;padding:2px 9px;white-space:nowrap;"><i class="ti ti-shield-half-filled" style="font-size:12px;"></i>'+vRisk+' risk</span>'
    +'<span style="font-size:10px;white-space:nowrap;color:'+vRcol+';font-weight:600;">'+vRisk+' '+_vb[vRisk]+'</span></div>';
  el.innerHTML='';
  sumEl.innerHTML='<div style="background:var(--bg2);border-radius:12px;padding:12px 14px;margin-bottom:10px;border-left:3px solid var(--info);">'
    +'<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px;">'
      +'<div>'
        +'<div style="font-size:16px;font-weight:700;color:var(--text);line-height:1.15;">'+(drName||v.plate)+ageTag(_drD.age)+'</div>'
        +'<div style="font-size:12px;color:var(--text2);">'+v.plate+' &middot; '+v.make+' &middot; '+v.trips.length+' trip'+(v.trips.length!==1?'s':'')+' scored</div>'
      +'</div>'
      +riskBlock
    +'</div>'
    +'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
      +(v.rank===1?'<span style="font-size:22px;">🥇</span>':v.rank===2?'<span style="font-size:22px;">🥈</span>':v.rank===3?'<span style="font-size:22px;">🥉</span>':'')
      +'<span style="font-size:11px;font-weight:500;color:var(--text2);">Fleet rank</span>'
      +'<span style="font-size:18px;font-weight:500;">#'+v.rank+'</span>'
      +'<span style="font-size:11px;color:var(--text2);">of '+vehicles.length+' vehicles</span>'
      +trendBadge
      +'<div style="margin-left:auto;display:flex;gap:4px;align-items:center;">'
      +[['Speeding',v.spd_avg],['Braking',v.brk_avg],['Acceleration',v.acc_avg],['Cornering',v.crn_avg]].map(function(p){
          var lbl=p[0],val=p[1];
          if(val===null||val===undefined)return'<div style="background:var(--bg3);border-radius:6px;padding:5px 10px;text-align:center;min-width:72px;"><div style="font-size:10px;color:var(--text2);">'+lbl+'</div><div style="font-size:12px;color:var(--text3);">—</div></div>';
          return'<div style="background:var(--bg3);border-radius:6px;padding:5px 10px;text-align:center;min-width:72px;"><div style="font-size:10px;color:var(--text2);">'+lbl+'</div><div style="font-size:14px;color:var(--gold);line-height:1;letter-spacing:1px;margin-top:3px;">'+starsN(val)+'</div></div>';
      }).join('')
      +'</div>'
      +(v.avg>=100?'<span style="font-size:14px;margin-right:3px;">🏆</span>':v.avg>=90?'<span style="font-size:14px;margin-right:3px;">💎</span>':'')
      +'<span style="font-size:20px;font-weight:500;color:'+sc(v.avg)+';">'+fmt1(v.avg)+'</span>'
    +'</div>'
    +(v.summary?'<p style="font-size:12px;color:var(--text2);line-height:1.65;margin-bottom:10px;">'+esc(v.summary)+'</p>':'')
    +predText
  +'</div>';

  v.trips.forEach(function(t){
    const score=(t.total!=null?t.total:tot(t));
    const comps=[
      {k:'Speeding',v:t.spd,w:t.lc?null:0.45,mx:45},
      {k:'Braking', v:t.brk,w:t.lc?0.364:0.20,mx:t.lc?36:20},
      {k:'Acceleration',v:t.acc,w:t.lc?0.273:0.15,mx:t.lc?27:15},
      {k:'Cornering',v:t.crn,w:t.lc?0.364:0.20,mx:t.lc?36:20}
    ];
    const cH=comps.map(function(c){
      if(c.v===null)return'<div class="comp"><div class="ck">'+c.k+'</div><div class="cs">—</div></div>';
      return'<div class="comp"><div class="ck">'+c.k+'</div><div class="cs">'+starsN(c.v)+'</div></div>';
    }).join('');
    const sz=tripSize(t.km);
    const szTag=sz==='short'?'<span class="tag ts" style="margin-left:4px;" data-tip="Short trip: under 10km">Short trip</span>'
               :sz==='long'?'<span class="tag tl" style="margin-left:4px;" data-tip="Long trip: 25km or more">Long trip</span>'
               :'<span class="tag" style="margin-left:4px;background:var(--bg3);color:var(--text2);border:0.5px solid var(--border);" data-tip="Standard trip: 10–24km">Standard</span>';
    const rpmTag=t.rpm_s>3?'<span class="tag" style="margin-left:4px;background:#3b0764;color:#d8b4fe;">⚡ High RPM</span>':'';
    const parts=t.t.split('→');
    const startTime=parts[0]||t.t;
    const endTime=parts[1]||'';
    const mapId='trip-map-'+t.id;
    const card=document.createElement('div');card.className='trip-card';
    card.style.cssText='display:flex;gap:12px;align-items:stretch;';
    const infoDiv=document.createElement('div');infoDiv.style.cssText='flex:1;min-width:0;';
    infoDiv.innerHTML=
      '<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;">'
        +'<div style="flex:1;">'
          +'<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
            +'<span style="font-size:13px;font-weight:600;color:var(--text);">'+(t.date||'')+'</span>'
            +'<span style="font-size:12px;color:var(--text2);">'+startTime+(endTime?' &rarr; '+endTime:'')+'</span>'
            +'<span style="font-size:11px;color:var(--text2);">&middot; '+t.km+'km</span>'
            +szTag+rpmTag
          +'</div>'
          +(t.from?'<div style="font-size:11px;color:var(--text3);margin-top:4px;"><i class="ti ti-map-pin" style="font-size:10px;vertical-align:-1px;color:var(--info);"></i> <span style="color:var(--text2);">'+t.from+'</span>'+(t.to?' &rarr; <span style="color:var(--text2);">'+t.to+'</span>':'')+'</div>':'')
        +'</div>'
        +'<div style="text-align:right;flex-shrink:0;">'
          +(score===100?'<span style="font-size:13px;margin-right:3px;">🏆</span>':score>=90?'<span style="font-size:13px;margin-right:3px;">💎</span>':'')+'<span style="font-size:20px;font-weight:500;color:'+sc(score)+';">'+fmt1(score)+'</span>'
        +'</div>'
      +'</div>'
      +'<div class="comps">'+cH+'</div>'
      +'<div style="margin-top:8px;display:flex;align-items:center;gap:8px;">'
        +'<span style="font-size:10px;color:var(--text3);white-space:nowrap;">Total</span>'
        +'<div class="bwrap" style="height:5px;"><div class="bfill" style="width:'+score+'%;background:'+sc(score)+';"></div></div>'
        +'<span style="font-size:11px;font-weight:500;color:'+sc(score)+';white-space:nowrap;">'+fmt1(score)+'/100</span>'
      +'</div>';
    card.appendChild(infoDiv);
    let mapDiv=null;
    if(t.slat&&t.slon&&t.elat&&t.elon){
      mapDiv=document.createElement('div');
      mapDiv.className='trip-map-sq';
      mapDiv.id=mapId;
      card.appendChild(mapDiv);
    }
    el.appendChild(card);
    if(mapDiv){
      mapDiv.dataset.slat=t.slat;mapDiv.dataset.slon=t.slon;
      mapDiv.dataset.elat=t.elat;mapDiv.dataset.elon=t.elon;
      mapDiv.dataset.tid=t.id;
      mapDiv.dataset.plate=plate;
      mapDiv.title='Click to expand — view route & event locations';
      mapDiv.addEventListener('click',function(){
        openTripMap(t.id,+t.slat,+t.slon,+t.elat,+t.elon,plate);
      });
      mapObserver.observe(mapDiv);
    }
  });
}

// A stored GPS track is only trustworthy if it actually spans the trip's real
// start/end. GPS gaps (cold start, signal loss) can leave a floating partial
// track; when that happens we fall back to the road route between the true
// endpoints so the map shows the correct trip rather than a stray segment.
function _trackSpansTrip(tk,slat,slon,elat,elon){
  if(!tk||tk.length<2) return false;
  function km(la1,lo1,la2,lo2){ var x=(la1-la2)*111, y=(lo1-lo2)*Math.cos(la1*Math.PI/180)*111; return Math.sqrt(x*x+y*y); }
  return km(tk[0][0],tk[0][1],slat,slon)<1.5 && km(tk[tk.length-1][0],tk[tk.length-1][1],elat,elon)<1.5;
}
function initLeafletMap(mapDiv){
  if(mapDiv._mapDone)return;mapDiv._mapDone=true;
  const slat=+mapDiv.dataset.slat,slon=+mapDiv.dataset.slon;
  const elat=+mapDiv.dataset.elat,elon=+mapDiv.dataset.elon;
  const tid=mapDiv.dataset.tid;
  const plate=mapDiv.dataset.plate;
  const map=L.map(mapDiv,{zoomControl:false,attributionControl:false,dragging:false,scrollWheelZoom:false,doubleClickZoom:false,touchZoom:false});
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
  const sIcon=L.divIcon({html:'<div style="background:#22c55e;width:10px;height:10px;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.6);"></div>',className:'',iconAnchor:[5,5]});
  const eIcon=L.divIcon({html:'<div style="background:#ef4444;width:10px;height:10px;border-radius:50%;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.6);"></div>',className:'',iconAnchor:[5,5]});
  L.marker([slat,slon],{icon:sIcon}).addTo(map);
  L.marker([elat,elon],{icon:eIcon}).addTo(map);
  map.fitBounds([[slat,slon],[elat,elon]],{padding:[18,18]});
  function osrm(){
    fetch('https://router.project-osrm.org/route/v1/driving/'+slon+','+slat+';'+elon+','+elat+'?overview=full&geometries=geojson')
      .then(function(r){return r.json();})
      .then(function(data){
        if(data.routes&&data.routes[0]){
          const coords=data.routes[0].geometry.coordinates.map(function(c){return[c[1],c[0]];});
          const pl=L.polyline(coords,{color:'#1e3a8a',weight:3,opacity:0.9}).addTo(map);
          map.fitBounds(pl.getBounds(),{padding:[18,18]});
        }
      }).catch(function(){});
  }
  // Small non-interactive event dots so the thumbnail flags that something happened.
  function drawThumbEvents(events){
    (events||[]).forEach(function(ev){
      var meta=EVENT_TYPES[ev.type]; if(!meta||ev.lat==null) return;
      L.circleMarker([ev.lat,ev.lon],{radius:3.5,color:'#fff',weight:1,fillColor:meta.col,fillOpacity:1,interactive:false}).addTo(map);
    });
  }
  // Prefer the real driven GPS track (lazy per-thumbnail); fall back to OSRM estimate
  if(tid){
    var _q=_sb.from('trip_tracks').select('track,events').eq('trip_id',tid);
    if(plate)_q=_q.eq('plate',plate); // trip ids can collide across vehicles — scope to this plate
    _q.maybeSingle().then(function(res){
      var data=(res&&!res.error&&res.data)?res.data:null;
      var tk=data?data.track:null;
      if(tk&&tk.length>1&&_trackSpansTrip(tk,slat,slon,elat,elon)){
        const pl=L.polyline(tk,{color:'#1e3a8a',weight:3,opacity:0.9}).addTo(map);
        map.fitBounds(pl.getBounds(),{padding:[14,14]});
      }else{osrm();}
      if(data) drawThumbEvents(data.events);
    }).catch(function(){osrm();});
  }else{osrm();}
}

// ── Expanded trip map: real GPS track + harsh-event locations ───────────────
const EVENT_TYPES={
  spd:{label:'Speeding',     col:'#ef4444', unit:' km/h over'},
  brk:{label:'Harsh braking',col:'#EF9F27', unit:' m/s²'},
  crn:{label:'Harsh cornering',col:'#a855f7', unit:' g'},
  acc:{label:'Harsh acceleration',col:'#85b7eb', unit:' m/s²'}
};
// Per-type severity by g-force: [moderate threshold, severe threshold] in g
const SEV_BANDS={crn:[0.50,0.65],acc:[0.31,0.39],brk:[0.46,0.56]};
function evSeverity(type,sev){
  if(sev==null)return null;
  var b=SEV_BANDS[type]; if(!b)return null;
  var g=(type==='crn')?sev:sev/9.81; // brk/acc sev is m/s² -> g; crn already g
  if(g>=b[1])return{lbl:'Severe',col:'#ef4444'};
  if(g>=b[0])return{lbl:'Moderate',col:'#EF9F27'};
  return{lbl:'Mild',col:'#97c459'};
}
let _tmMap=null;
function _ensureTmOverlay(){
  let ov=document.getElementById('tm-overlay');
  if(ov)return ov;
  ov=document.createElement('div');ov.id='tm-overlay';ov.className='tm-overlay';
  ov.innerHTML='<div class="tm-modal">'
    +'<div class="tm-head"><span class="ttl" id="tm-title">Trip</span>'
    +'<button class="tm-close" id="tm-close" aria-label="Close">&times;</button></div>'
    +'<div class="tm-map" id="tm-map"></div>'
    +'<div class="tm-legend" id="tm-legend"></div></div>';
  document.body.appendChild(ov);
  function close(){ ov.classList.remove('open'); if(_tmMap){_tmMap.remove();_tmMap=null;} }
  ov.addEventListener('click',function(e){ if(e.target===ov)close(); });
  ov.querySelector('#tm-close').addEventListener('click',close);
  document.addEventListener('keydown',function(e){ if(e.key==='Escape'&&ov.classList.contains('open'))close(); });
  return ov;
}
async function openTripMap(tripId,slat,slon,elat,elon,plate){
  const ov=_ensureTmOverlay();
  ov.classList.add('open');
  document.getElementById('tm-title').textContent='Trip #'+tripId+' — route & events';
  document.getElementById('tm-legend').innerHTML='';
  const mapEl=document.getElementById('tm-map');
  if(_tmMap){_tmMap.remove();_tmMap=null;}
  mapEl.innerHTML='';
  const map=L.map(mapEl,{zoomControl:true,attributionControl:false});
  _tmMap=map;
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
  const sIcon=L.divIcon({html:'<div class="ev-pin" style="background:#22c55e;width:14px;height:14px;"></div>',className:'',iconAnchor:[7,7]});
  const eIcon=L.divIcon({html:'<div class="ev-pin" style="background:#ef4444;width:14px;height:14px;"></div>',className:'',iconAnchor:[7,7]});
  L.marker([slat,slon],{icon:sIcon}).addTo(map).bindPopup('Start');
  L.marker([elat,elon],{icon:eIcon}).addTo(map).bindPopup('End');
  map.fitBounds([[slat,slon],[elat,elon]],{padding:[30,30]});
  setTimeout(function(){ map.invalidateSize(); },60);

  // Lazy-fetch the track + events for this trip
  let row=null;
  try{
    let _q=_sb.from('trip_tracks').select('track,events').eq('trip_id',tripId);
    if(plate)_q=_q.eq('plate',plate); // trip ids can collide across vehicles — scope to this plate
    const res=await _q.maybeSingle();
    if(!res.error) row=res.data;
  }catch(e){}

  if(row&&row.track&&row.track.length>1&&_trackSpansTrip(row.track,slat,slon,elat,elon)){
    const pl=L.polyline(row.track,{color:'#1e3a8a',weight:4,opacity:0.9}).addTo(map);
    map.fitBounds(pl.getBounds(),{padding:[30,30]});
  } else {
    // fallback: OSRM estimated route between start/end
    fetch('https://router.project-osrm.org/route/v1/driving/'+slon+','+slat+';'+elon+','+elat+'?overview=full&geometries=geojson')
      .then(function(r){return r.json();})
      .then(function(data){
        if(data.routes&&data.routes[0]){
          const c=data.routes[0].geometry.coordinates.map(function(p){return[p[1],p[0]];});
          const pl=L.polyline(c,{color:'#1e3a8a',weight:4,opacity:0.9,dashArray:'5,6'}).addTo(map);
          map.fitBounds(pl.getBounds(),{padding:[30,30]});
        }
      }).catch(function(){});
  }

  // Event markers + legend
  const counts={spd:0,brk:0,crn:0,acc:0};
  const events=(row&&row.events)||[];
  events.forEach(function(ev){
    const meta=EVENT_TYPES[ev.type]; if(!meta)return;
    counts[ev.type]=(counts[ev.type]||0)+1;
    const sz=ev.type==='spd'?13:11;
    const icon=L.divIcon({html:'<div class="ev-pin" style="background:'+meta.col+';width:'+sz+'px;height:'+sz+'px;"></div>',className:'',iconAnchor:[sz/2,sz/2]});
    const when=ev.ts?new Date(ev.ts*1000).toLocaleString([],{weekday:'short',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'';
    var sv=evSeverity(ev.type,ev.sev);
    var sevTag=sv?' <span style="color:'+sv.col+';font-weight:600;">'+sv.lbl+'</span>':'';
    var detail=(ev.type==='spd'&&ev.spd!=null&&ev.lim!=null)
      ? '<b>'+ev.spd+' km/h</b> in a '+ev.lim+' km/h zone'+(ev.sev!=null?' (+'+ev.sev+' over)':'')
      : '';  // harsh brk/acc/crn: show type + severity only, no raw g value
    L.marker([ev.lat,ev.lon],{icon:icon}).addTo(map)
      .bindPopup('<b style="color:'+meta.col+'">'+meta.label+'</b>'+sevTag+'<br>'+(detail?detail+'<br>':'')+when);
  });
  const legend=document.getElementById('tm-legend');
  Object.keys(EVENT_TYPES).forEach(function(k){
    const meta=EVENT_TYPES[k];
    const li=document.createElement('span');li.className='li';
    li.innerHTML='<span class="dot" style="background:'+meta.col+';"></span>'+meta.label
      +' <span class="ct">('+(counts[k]||0)+')</span>';
    legend.appendChild(li);
  });
  if(!events.length){
    const note=document.createElement('span');note.className='li';note.style.color='var(--text3)';
    note.textContent=row?'No harsh events recorded on this trip.':'Detailed track not yet available — showing estimated route.';
    legend.appendChild(note);
  }
}

const mapObserver=new IntersectionObserver(function(entries){
  entries.forEach(function(e){if(e.isIntersecting)initLeafletMap(e.target);});
},{threshold:0.1});

// Fleet breakdown panel — fleet-wide component stars + risk badge + weekly AI summary.
// Populated from latest_run.data.fleet_insight (cached/regenerated every Monday).
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
var _riskBands={low:90.0,high:70.0}; // risk thresholds; synced from fleet_insight, reused for per-driver risk
function renderFleetInsight(fi){
  var el=document.getElementById('tier-breakdown');
  if(!el) return;
  if(!fi){ el.innerHTML=''; return; }
  var rcolMap={Low:'var(--success)',Moderate:'var(--warning)',High:'var(--danger)'};
  var rcol=rcolMap[fi.risk]||'var(--text3)';
  function f1(x){return (+x).toFixed(1);}

  // component stars
  var comps=[['Speeding',fi.spd],['Braking',fi.brk],['Acceleration',fi.acc],['Cornering',fi.crn]];
  var rows=comps.map(function(c){
    var stars=(c[1]==null)?'<span style="color:var(--text3);font-size:11px;">—</span>':starsN(c[1]);
    return '<div style="display:flex;align-items:center;gap:8px;">'
      +'<span style="font-size:11px;color:var(--text2);min-width:84px;">'+c[0]+'</span>'
      +'<span style="font-size:14px;line-height:1;letter-spacing:1px;">'+stars+'</span></div>';
  }).join('');

  // risk badge + score-band legend (so they can see how close they are to the next band)
  var lo=(fi.risk_low_min!=null?fi.risk_low_min:85), hi=(fi.risk_high_max!=null?fi.risk_high_max:70);
  _riskBands={low:+lo,high:+hi}; // remember the live thresholds for the per-driver badge
  var badge=fi.risk?('<span style="display:inline-flex;align-items:center;gap:5px;font-size:10px;font-weight:600;'
    +'letter-spacing:.5px;text-transform:uppercase;color:'+rcol+';border:1px solid '+rcol+';border-radius:11px;padding:2px 9px;">'
    +'<i class="ti ti-shield-half-filled" style="font-size:12px;"></i>'+esc(fi.risk)+' risk</span>'):'';
  // show only the current band's range (its edges still tell you when you'd cross into the next band)
  var bands={High:'&lt;'+f1(hi),Moderate:f1(hi)+'–'+f1(lo),Low:'&ge;'+f1(lo)};
  var legend=(fi.risk&&bands[fi.risk])?('<span style="color:'+rcol+';font-weight:600;">'+esc(fi.risk)+' '+bands[fi.risk]+'</span>'):'';
  var riskBlock=fi.risk?('<div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;">'
    +badge+'<span style="font-size:10px;white-space:nowrap;">'+legend+'</span></div>'):'';

  // trip mix (short / standard / long)
  var sh=fi.short||0, st=fi.standard||0, lg=fi.long||0, tot=sh+st+lg;
  function pct(n){return tot?Math.round(n/tot*100):0;}
  function seg(n,lab){return '<span><b style="color:var(--text);">'+n+'</b> '+lab
    +' <span style="color:var(--text2);">('+pct(n)+'%)</span></span>';}
  var mix=tot?('<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:9px;font-size:12px;color:var(--text2);">'
    +'<span style="color:var(--text2);text-transform:uppercase;letter-spacing:.5px;font-size:10px;font-weight:600;">Trip mix</span>'
    +seg(sh,'short')+seg(st,'standard')+seg(lg,'long')+'</div>'):'';

  el.innerHTML=
    '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:9px;">'
      +'<span style="font-size:11px;letter-spacing:.8px;color:var(--text2);text-transform:uppercase;padding-top:3px;">Fleet breakdown</span>'
      +riskBlock+'</div>'
    +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:5px 22px;margin-bottom:9px;">'+rows+'</div>'
    +mix
    +(fi.summary?('<div style="font-size:11px;line-height:1.45;color:var(--text2);">'+esc(fi.summary)+'</div>'):'');
}



var _lastUpdatedAt=null;
async function loadDashboardData(){
  var res=await _sb.from('latest_run').select('data, updated_at').eq('id',1).single();
  if(res.error||!res.data){ console.error('Failed to load data',res.error); return; }
  if(_lastUpdatedAt&&res.data.updated_at===_lastUpdatedAt) return;
  _lastUpdatedAt=res.data.updated_at;
  var d=res.data.data;
  vehicles=d.vehicles||[];
  incData=d.incidents||[];
  weeksData=d.weeks||[];
  // Stat cards
  var fa=d.fleet_avg||0, ft=d.fleet_trend||0;
  document.getElementById('s-avg').textContent=fmt1(fa);
  document.getElementById('s-stars').innerHTML=starsN(fa);
  document.getElementById('s-trips').textContent=d.total_trips||'';
  renderFleetInsight(d.fleet_insight);
  document.querySelectorAll('.s-range').forEach(function(el){ el.textContent=d.date_range||''; });
  var upEl=document.getElementById('s-updated');
  if(upEl&&res.data.updated_at){
    var dt=new Date(res.data.updated_at);
    upEl.textContent=dt.toLocaleDateString([],{day:'2-digit',month:'short',year:'numeric'})
      +' '+dt.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
  }
  // Recalculate leaderboard week index
  var now=Date.now()/1000;
  lbIdx=Math.max(0,weeksData.length-1);
  for(var i=weeksData.length-1;i>=0;i--){
    if(weeksData[i].ts+7*86400<now){ lbIdx=i; break; }
  }
}
// ── Auth gate ──────────────────────────────────────────────────────────────
var _appStarted=false;
function startApp(){
  if(_appStarted) return; _appStarted=true;
  Promise.all([initDrivers(),loadDashboardData()]).then(function(){ renderRanking(); if(vehicles.length>0)selectV(vehicles[0].plate); });
  setInterval(function(){ loadDashboardData().then(function(){ renderRanking(); }); }, 5*60*1000);
}
function showLogin(){
  // drop any expired-link UI so it can't linger on a later, non-error login
  ['relink-banner','relink-btn','relink-status'].forEach(function(id){ var n=document.getElementById(id); if(n&&n.parentNode) n.parentNode.removeChild(n); });
  document.getElementById('setpw-view').style.display='none';
  document.getElementById('app-root').style.display='none';
  document.getElementById('login-view').style.display='flex';
}
async function showApp(){
  _linkExpiredShown=false; // leaving the expired-link screen
  var auth=await isAuthorized();
  if(auth==='denied'){ return unauthorizedSignOut(); }
  if(auth==='error'){ return verifyFailed(); }
  document.getElementById('login-view').style.display='none';
  document.getElementById('setpw-view').style.display='none';
  document.getElementById('app-root').style.display='block';
  startApp();
  loadRole();
  recordLogin();
}
var _loginRecorded=false;
function recordLogin(){
  if(_loginRecorded) return; _loginRecorded=true;
  try{ _adminCall({action:'record_login'}).catch(function(){}); }catch(e){}
}
// Invited-only gate. Returns 'ok' (admin or allow-listed), 'denied' (signed in but
// not invited), or 'error' (couldn't verify: RLS/network problem). Only the genuine
// table-missing case fails open (pre-lockdown); any other error becomes 'error' so a
// misconfig shows a clear message instead of a silently blank dashboard.
async function isAuthorized(){
  try{
    var u=await _sb.auth.getUser(); var user=u&&u.data&&u.data.user; if(!user) return 'denied';
    var pr=await _sb.from('profiles').select('role').eq('id',user.id).maybeSingle();
    if(pr&&pr.data&&pr.data.role==='admin') return 'ok';
    var email=(user.email||'').toLowerCase();
    var al=await _sb.from('allowed_emails').select('email').eq('email',email).maybeSingle();
    if(al&&al.error){
      var c=al.error.code||'', m=al.error.message||'';
      if(c==='42P01'||/relation .*does not exist|could not find the table/i.test(m)) return 'ok'; // allowlist not created yet (pre-lockdown)
      return 'error'; // RLS/network problem -> diagnosable, don't mask as a blank app
    }
    return (al&&al.data) ? 'ok' : 'denied';
  }catch(e){ return 'error'; }
}
async function unauthorizedSignOut(){
  try{ await _sb.auth.signOut(); }catch(e){}
  document.getElementById('app-root').style.display='none';
  document.getElementById('setpw-view').style.display='none';
  document.getElementById('login-view').style.display='flex';
  var e=document.getElementById('login-err');
  if(e){ e.textContent="This account isn't authorised yet — ask an admin to invite your email."; e.style.display='block'; }
}
// Couldn't verify access (RLS/network). Keep the session so a refresh retries; show a
// clear message rather than signing out or rendering an empty dashboard.
function verifyFailed(){
  document.getElementById('app-root').style.display='none';
  document.getElementById('setpw-view').style.display='none';
  document.getElementById('login-view').style.display='flex';
  var e=document.getElementById('login-err');
  if(e){ e.textContent="Couldn't verify your access right now. Please refresh the page — if this keeps happening, contact an admin."; e.style.display='block'; }
}
function showSetPassword(){
  document.getElementById('login-view').style.display='none';
  document.getElementById('app-root').style.display='none';
  document.getElementById('setpw-view').style.display='flex';
}
async function doLogin(ev){
  if(ev) ev.preventDefault();
  var btn=document.getElementById('login-btn'), err=document.getElementById('login-err');
  err.style.display='none'; var lm=document.getElementById('login-msg'); if(lm) lm.style.display='none';
  var email=(document.getElementById('login-email').value||'').trim();
  var pw=document.getElementById('login-pw').value||'';
  if(!email||!pw){ err.textContent='Enter your email and password.'; err.style.display='block'; return; }
  btn.disabled=true; btn.textContent='Signing in…';
  var res=await _sb.auth.signInWithPassword({email:email,password:pw});
  btn.disabled=false; btn.textContent='Sign in';
  if(res.error){ err.textContent=res.error.message||'Sign-in failed.'; err.style.display='block'; return; }
  showApp();
}
// Self-service password reset from the login screen. Sends a recovery email; the
// link returns to this page (#type=recovery) -> the set-password screen.
async function forgotPassword(){
  var err=document.getElementById('login-err'), msg=document.getElementById('login-msg');
  var email=(document.getElementById('login-email').value||'').trim();
  err.style.display='none'; if(msg) msg.style.display='none';
  if(!email){ err.textContent='Enter your email above first, then try again.'; err.style.display='block'; return; }
  var link=document.getElementById('forgot-link'), lt=link?link.textContent:'';
  if(link){ link.textContent='Sending…'; link.style.pointerEvents='none'; }
  var r=await _sb.auth.resetPasswordForEmail(email,{redirectTo:location.origin+location.pathname});
  if(link){ link.textContent=lt; link.style.pointerEvents=''; }
  if(r&&r.error&&/rate|too many|429/i.test((r.error.message||'')+' '+(r.error.status||''))){ err.textContent='Too many requests — please wait a minute and try again.'; err.style.display='block'; return; }
  // any other SDK error falls through to the SAME neutral message so we never reveal whether the account exists
  if(msg){ msg.textContent='If an account exists for '+email+', a password-reset link is on its way — check your inbox.'; msg.style.display='block'; }
}
// Friendly handling for an expired/already-used sign-in link: explain it on the login
// screen and offer a one-click "Email me a new link" (reuses the recovery flow above).
function showLinkExpired(){
  _linkExpiredShown=true;
  showLogin();
  var form=document.getElementById('login-form');
  var err=document.getElementById('login-err'), msg=document.getElementById('login-msg');
  if(msg) msg.style.display='none';
  if(err) err.style.display='none'; // the banner carries the explanation; keep the red slot for real errors
  var blob=((_hashErr&&(_hashErr.code+' '+_hashErr.error+' '+_hashErr.desc))||'').toLowerCase();
  var expired=/expired|otp_expired/.test(blob);
  if(form && !document.getElementById('relink-banner')){
    var ban=document.createElement('div');
    ban.id='relink-banner';
    ban.style.cssText='background:var(--warning-bg);border:0.5px solid var(--warning);border-radius:8px;padding:10px 12px;font-size:12px;line-height:1.5;color:var(--text);margin-bottom:18px;';
    ban.innerHTML=expired
      ? '<b>This link has expired or was already used.</b><br>Enter your email below and we’ll send you a fresh one (valid 24 hours).'
      : '<b>That sign-in link didn’t work.</b><br>Enter your email below and we’ll send you a new one.';
    var anchor=form.querySelector('label[for="login-email"]')||form.firstChild; // sit under the brand, above the form
    form.insertBefore(ban, anchor);
  }
  var loginBtn=document.getElementById('login-btn');
  if(loginBtn && !document.getElementById('relink-btn')){
    var b=document.createElement('button');
    b.id='relink-btn'; b.type='button'; b.textContent='Email me a new link';
    // secondary/outline style so it doesn't compete with the green primary "Sign in" and stays legible in both themes
    b.style.cssText='width:100%;margin-top:10px;background:transparent;color:var(--info);font-weight:600;font-size:13px;border:1px solid var(--info);border-radius:8px;padding:9px;cursor:pointer;';
    b.onclick=function(){ requestNewLink(b); };
    loginBtn.parentNode.insertBefore(b, loginBtn.nextSibling);
    var st=document.createElement('div'); st.id='relink-status';
    st.style.cssText='display:none;font-size:12px;line-height:1.4;margin-top:8px;';
    b.parentNode.insertBefore(st, b.nextSibling); // feedback lands right under the button the user clicked
  }
  _clearAuthHash();
}
// Self-service "email me a new link" from the expired-link screen. Feedback shows in
// #relink-status (directly under the button) and flips the banner out of its stale
// "expired" copy. Always neutral on a backend error, so it never reveals whether the
// account exists; only genuine rate-limiting is surfaced distinctly.
async function requestNewLink(btn){
  var email=(document.getElementById('login-email').value||'').trim();
  var st=document.getElementById('relink-status'), ban=document.getElementById('relink-banner');
  function show(txt,col){ if(!st)return; st.textContent=txt; st.style.color=col; st.style.display='block'; }
  if(!email){ show('Enter your email above first, then tap “Email me a new link”.','var(--danger)'); return; }
  var t=btn.textContent; btn.disabled=true; btn.textContent='Sending…';
  var r=null; try{ r=await _sb.auth.resetPasswordForEmail(email,{redirectTo:location.origin+location.pathname}); }catch(e){ r={error:{message:'network'}}; }
  btn.disabled=false; btn.textContent=t;
  if(r&&r.error&&/rate|too many|429/i.test((r.error.message||'')+' '+(r.error.status||''))){ show('Too many requests — please wait a minute, then try again.','var(--danger)'); return; }
  show('New link sent — check your inbox (it can take a minute, and may land in spam).','var(--success)');
  if(ban){ ban.style.borderColor='var(--success)'; ban.style.background='var(--success-bg)'; ban.innerHTML='<b>New link on its way.</b><br>Open it on this device to finish signing in.'; }
}
async function ssoLogin(provider){
  var err=document.getElementById('login-err'); err.style.display='none';
  var r=await _sb.auth.signInWithOAuth({provider:provider,options:{redirectTo:location.origin+location.pathname,scopes:provider==='azure'?'email':undefined}});
  if(r&&r.error){ err.textContent=r.error.message||'Could not start sign-in.'; err.style.display='block'; }
}
async function doSetPassword(ev){
  if(ev) ev.preventDefault();
  var err=document.getElementById('setpw-err'); err.style.display='none';
  var pw=document.getElementById('setpw-pw').value||'', pw2=document.getElementById('setpw-pw2').value||'';
  if(pw.length<8){ err.textContent='Password must be at least 8 characters.'; err.style.display='block'; return; }
  if(pw!==pw2){ err.textContent='Passwords do not match.'; err.style.display='block'; return; }
  var btn=document.getElementById('setpw-btn'); btn.disabled=true; btn.textContent='Saving…';
  var res=await _sb.auth.updateUser({password:pw});
  btn.disabled=false; btn.textContent='Set password & continue';
  if(res.error){ err.textContent=res.error.message||'Could not set password.'; err.style.display='block'; return; }
  _hashType=''; try{ history.replaceState(null,'',location.pathname+location.search); }catch(e){}
  showApp();
}
async function doLogout(){ try{ await _sb.auth.signOut(); }catch(e){} location.reload(); }

// ── Roles ────────────────────────────────────────────────────────────────
async function loadRole(){
  try{
    var u=await _sb.auth.getUser(); currentUser=(u&&u.data&&u.data.user)||null;
    if(currentUser){
      var r=await _sb.from('profiles').select('role').eq('id',currentUser.id).maybeSingle();
      currentRole=(r&&r.data&&r.data.role)||'user';
    }
  }catch(e){ currentRole='user'; }
  applyRoleUI();
}
function applyRoleUI(){
  var isAdmin=currentRole==='admin';
  var at=document.getElementById('tab-admin'); if(at) at.style.display=isAdmin?'':'none';
  var dt=document.getElementById('tab-drivers'); if(dt) dt.style.display=isAdmin?'':'none';
  if(!isAdmin){
    var va=document.getElementById('view-admin'), vd=document.getElementById('view-drivers');
    if((va&&va.style.display!=='none')||(vd&&vd.style.display!=='none')) switchTab('dash');
  }
}

// ── Admin section ──────────────────────────────────────────────────────────
async function _adminCall(payload){
  var sess=await _sb.auth.getSession();
  var token=sess&&sess.data&&sess.data.session&&sess.data.session.access_token;
  var res=await fetch(SUPA_URL+'/functions/v1/admin-users',{
    method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+token,'apikey':SUPA_KEY},
    body:JSON.stringify(payload)
  });
  var out={}; try{ out=await res.json(); }catch(e){}
  if(!res.ok) throw new Error(out.error||('Request failed ('+res.status+')'));
  return out;
}
function _adminMsg(text,color){
  var m=document.getElementById('inv-msg'); if(!m) return;
  m.textContent=text; m.style.color=color||'var(--text2)'; m.style.display='block';
}
var _adminUsersCache=null, _editUserId=null;
async function loadAdminUsers(){
  var el=document.getElementById('admin-users'); if(!el) return;
  // Cache: render the last-known list instantly, then refresh in the background
  // and only re-render if it actually changed (so it loads quick and doesn't
  // wipe an in-progress edit on every tab switch).
  if(_adminUsersCache) renderAdminUsers(_adminUsersCache);
  else el.innerHTML='<div class="empty">Loading…</div>';
  try{
    var out=await _adminCall({action:'list'});
    var fresh=out.users||[];
    var changed=!_adminUsersCache || JSON.stringify(_adminUsersCache)!==JSON.stringify(fresh);
    _adminUsersCache=fresh;
    if(changed) renderAdminUsers(fresh);
  }catch(e){
    if(!_adminUsersCache) el.innerHTML='<div class="empty" style="text-align:left;line-height:1.6;">Could not load users: '+esc(e.message)
      +'<br><span style="font-size:11px;color:var(--text3);">Make sure the <b>admin-users</b> Edge Function is deployed in Supabase.</span></div>';
  }
}
function _fmtLogin(ts){
  if(!ts) return '<span style="color:var(--text3);">Never</span>';
  var d=new Date(ts); if(isNaN(d.getTime())) return '<span style="color:var(--text3);">&mdash;</span>';
  var mon=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
  var h=d.getHours(), m=d.getMinutes(), ap=h>=12?'pm':'am'; h=h%12||12;
  return d.getDate()+' '+mon+' '+(''+d.getFullYear()).slice(2)+', '+h+':'+(m<10?'0':'')+m+ap;
}
function _splitName(nm){ var p=(nm||'').trim().split(/\s+/).filter(Boolean); var f=p.shift()||''; return {first:f, last:p.join(' ')}; }
function renderAdminUsers(users){
  var el=document.getElementById('admin-users');
  if(!users.length){ el.innerHTML='<div class="empty">No users yet.</div>'; return; }
  var head='<div class="au-head"><span>Email</span><span>First name</span><span>Last name</span><span>Role</span><span>Status</span><span>Last login</span><span>Actions</span></div>';
  el.innerHTML=head+users.map(function(u){
    var isMe=currentUser&&u.id===currentUser.id, pending=!u.confirmed, nm=_splitName(u.name);
    var status='<span class="tag" style="background:'+(pending?'var(--warning-bg)':'var(--success-bg)')+';color:'+(pending?'var(--warning)':'var(--success)')+';">'+(pending?'pending':'active')+'</span>';
    var statusCell='<div class="au-status">'+status+(isMe?'<span style="font-size:10px;color:var(--text3);">you</span>':'')+'</div>';
    var loginCell='<div class="au-cell" style="font-size:11px;color:var(--text2);" title="'+esc(u.last_sign_in_at||'')+'">'+_fmtLogin(u.last_sign_in_at)+'</div>';
    if(u.id===_editUserId){
      return '<div class="au-row">'
        +'<input id="u-email-'+u.id+'" type="email" value="'+esc(u.email)+'">'
        +'<input id="u-first-'+u.id+'" type="text" placeholder="First" value="'+esc(nm.first)+'">'
        +'<input id="u-last-'+u.id+'" type="text" placeholder="Last" value="'+esc(nm.last)+'">'
        +'<select id="u-role-'+u.id+'" '+(isMe?'disabled style="opacity:.6;"':'')+'>'
          +'<option value="user"'+(u.role==='user'?' selected':'')+'>user</option>'
          +'<option value="admin"'+(u.role==='admin'?' selected':'')+'>admin</option>'
        +'</select>'
        +statusCell
        +loginCell
        +'<div class="au-actions">'
          +'<button class="au-btn save" onclick="saveUser(\''+u.id+'\')">Save</button>'
          +'<button class="au-btn" onclick="cancelEditUser()">Cancel</button>'
        +'</div>'
      +'</div>';
    }
    var roleBadge='<span class="tag" style="background:'+(u.role==='admin'?'var(--info-bg)':'var(--bg3)')+';color:'+(u.role==='admin'?'var(--info)':'var(--text2)')+';text-transform:capitalize;">'+(u.role||'user')+'</span>';
    var loginCellRO='<div class="au-cell au-login" onclick="toggleDetail(\''+u.id+'\')" title="Click for last IP &amp; approx location" style="cursor:pointer;font-size:11px;color:var(--text2);">'+_fmtLogin(u.last_sign_in_at)+' <span style="color:var(--text3);font-size:9px;">&#9662;</span></div>';
    return '<div class="au-item">'
      +'<div class="au-row">'
        +'<div class="au-cell" title="'+esc(u.email)+'">'+esc(u.email)+'</div>'
        +'<div class="au-cell">'+(nm.first?esc(nm.first):'<span style="color:var(--text3);">&mdash;</span>')+'</div>'
        +'<div class="au-cell">'+(nm.last?esc(nm.last):'<span style="color:var(--text3);">&mdash;</span>')+'</div>'
        +'<div>'+roleBadge+'</div>'
        +statusCell
        +loginCellRO
        +'<div class="au-actions">'
          +'<button class="au-btn" onclick="editUser(\''+u.id+'\')"><i class="ti ti-pencil" style="font-size:12px;vertical-align:-1px;"></i> Edit</button>'
          +(pending
              ?'<button class="au-btn" title="Resend invite email" onclick="resendInvite(\''+u.id+'\')">Resend</button>'
              :'<button class="au-btn" title="Send password-reset email" onclick="resetPassword(\''+u.id+'\')">Reset</button>')
          +(isMe?'':'<button class="au-btn del" title="Remove user" onclick="removeUser(\''+u.id+'\')"><i class="ti ti-trash" style="font-size:12px;"></i></button>')
        +'</div>'
      +'</div>'
      +'<div class="au-detail" id="au-detail-'+u.id+'" style="display:none;">'
        +'Last login IP: <b id="au-ip-'+u.id+'">&hellip;</b><span style="color:var(--text3);"> &middot; </span>Approx location: <span id="au-geo-'+u.id+'">&hellip;</span>'
      +'</div>'
    +'</div>';
  }).join('');
}
async function toggleDetail(id){
  var d=document.getElementById('au-detail-'+id); if(!d) return;
  if(d.style.display!=='none'){ d.style.display='none'; return; }
  d.style.display='';
  if(d._loaded) return; d._loaded=true;
  var u=(_adminUsersCache||[]).find(function(x){return x.id===id;})||{};
  var ip=(u.ip||'').trim();
  var ipEl=document.getElementById('au-ip-'+id), geoEl=document.getElementById('au-geo-'+id);
  if(ipEl) ipEl.textContent=ip||'not recorded yet';
  if(!ip){ if(geoEl) geoEl.textContent='—'; return; }
  if(u.loc){ if(geoEl) geoEl.textContent=u.loc; return; }
  if(geoEl) geoEl.textContent='locating…';
  try{ var g=await _adminCall({action:'geoip',ip:ip}); var loc=[g.city,g.country].filter(Boolean).join(', '); if(geoEl) geoEl.textContent=loc||'unknown'; }
  catch(e){ if(geoEl) geoEl.textContent='unavailable'; }
}
function _uEmail(id){
  var e=document.getElementById('u-email-'+id);
  if(e) return (e.value||'').trim();
  var u=(_adminUsersCache||[]).find(function(x){return x.id===id;});
  return u?(u.email||''):'';
}
function editUser(id){ _editUserId=id; if(_adminUsersCache) renderAdminUsers(_adminUsersCache); }
function cancelEditUser(){ _editUserId=null; if(_adminUsersCache) renderAdminUsers(_adminUsersCache); }
async function saveUser(id){
  var roleEl=document.getElementById('u-role-'+id);
  var fEl=document.getElementById('u-first-'+id), lEl=document.getElementById('u-last-'+id);
  var name=((fEl?fEl.value:'').trim()+' '+(lEl?lEl.value:'').trim()).trim();
  var payload={action:'update',id:id,email:_uEmail(id),name:name};
  if(roleEl && !roleEl.disabled) payload.role=roleEl.value;
  try{ await _adminCall(payload); _editUserId=null; _adminMsg('Saved.','var(--success)'); loadAdminUsers(); }
  catch(e){ _adminMsg(e.message,'var(--danger)'); }
}
async function removeUser(id){
  var email=_uEmail(id);
  if(!confirm('Remove '+email+'? They will lose access immediately.')) return;
  try{ await _adminCall({action:'remove',id:id}); _adminMsg('Removed '+email+'.','var(--success)'); loadAdminUsers(); }
  catch(e){ _adminMsg(e.message,'var(--danger)'); }
}
async function resendInvite(id){
  try{ await _adminCall({action:'resend',email:_uEmail(id)}); _adminMsg('Invite re-sent to '+_uEmail(id)+'.','var(--success)'); }
  catch(e){ _adminMsg(e.message,'var(--danger)'); }
}
async function resetPassword(id){
  var email=_uEmail(id);
  try{ var r=await _sb.auth.resetPasswordForEmail(email,{redirectTo:location.origin+location.pathname});
    if(r.error) throw new Error(r.error.message);
    _adminMsg('Password-reset email sent to '+email+'.','var(--success)'); }
  catch(e){ _adminMsg(e.message,'var(--danger)'); }
}
async function sendInvite(ev){
  if(ev) ev.preventDefault();
  var email=(document.getElementById('inv-email').value||'').trim();
  var first=(document.getElementById('inv-first').value||'').trim();
  var last=(document.getElementById('inv-last').value||'').trim();
  var role=(document.getElementById('inv-role').value||'user');
  var name=(first+' '+last).trim();
  var btn=document.getElementById('inv-btn');
  if(!email){ _adminMsg('Enter an email address.','var(--danger)'); return; }
  btn.disabled=true; btn.textContent='Sending…';
  try{
    await _adminCall({action:'invite',email:email,name:name,role:role});
    _adminMsg('Invite sent to '+email+(role==='admin'?' as an admin':'')+'.','var(--success)');
    document.getElementById('inv-email').value='';
    document.getElementById('inv-first').value='';
    document.getElementById('inv-last').value='';
    document.getElementById('inv-role').value='user';
    _adminUsersCache=null; loadAdminUsers();
  }catch(e){ _adminMsg(e.message||'Invite failed.','var(--danger)'); }
  btn.disabled=false; btn.textContent='Send invite';
}

_sb.auth.getSession().then(function(res){
  var sess=res&&res.data&&res.data.session;
  if(_hashErr && !sess){ showLinkExpired(); return; } // failed link (incl. expired recovery/invite) -> friendly banner, before the set-password branch
  if(_hashType==='invite'||_hashType==='recovery'){ showSetPassword(); return; }
  if(_hashErr){ _clearAuthHash(); showApp(); return; } // _hashErr && sess: already signed in, just scrub the stale error
  if(sess) showApp(); else showLogin();
});
_sb.auth.onAuthStateChange(function(event,session){
  if((_hashType==='invite'||_hashType==='recovery') && session){ showSetPassword(); return; }
  if(_linkExpiredShown && !session){ return; } // keep the expired-link screen up; don't flash a blank login over it
  if(event==='SIGNED_OUT' || !session) showLogin();
});

// ── Leaderboard ────────────────────────────────────────────────────────────
// Default to last fully-completed week (Sunday has passed)
let lbIdx=0; // recalculated by loadDashboardData()

function changeWeek(dir){
  lbIdx=Math.max(0,Math.min(weeksData.length-1,lbIdx+dir));
  renderLeaderboard();
}

function renderLeaderboard(){
  var drData=loadDrivers();
  function drName(plate){ var d=drData[plate]||{}; var f=(d.first||'').trim(),l=(d.last||'').trim().toUpperCase(); return f||l?esc(f+(l?(' '+l+'.'):'')):''; }
  function drAge(plate){ return (drData[plate]||{}).age||''; }
  function ageTag(a){
    var m={
      'Under 25':['#c084fc','rgba(139,92,246,0.18)'],
      '25–34':['var(--info)','var(--info-bg)'],
      '35–44':['#5eead4','rgba(20,184,166,0.15)'],
      '45–54':['var(--warning)','var(--warning-bg)'],
      '55–64':['var(--success)','var(--success-bg)'],
      '65+':['#f87171','var(--danger-bg)']
    };
    var c=m[a];
    return a&&c?'<span style="font-size:9px;font-weight:600;padding:1px 5px;border-radius:3px;background:'+c[1]+';color:'+c[0]+';margin-left:5px;">'+a+'</span>':'';
  }
  const wk=weeksData[lbIdx];
  document.getElementById('week-label').textContent=wk.label;
  document.getElementById('btn-prev').disabled=lbIdx===0;
  document.getElementById('btn-next').disabled=lbIdx===weeksData.length-1;

  const top3=wk.rankings.slice(0,3);
  const medals=['🥇','🥈','🥉'];
  const pClass=['p1','p2','p3'];
  const podiumOrder=top3.length>=2?[1,0,2]:[0];
  const podEl=document.getElementById('podium');podEl.innerHTML='';
  podiumOrder.forEach(function(idx){
    if(idx>=top3.length)return;
    const r=top3[idx];
    const slot=document.createElement('div');slot.className='podium-slot';
    slot.innerHTML=
      '<div class="podium-card '+pClass[idx]+'">'
        +'<span class="podium-trend">'+trendIcon(r.trend,14)+'</span>'
        +'<span class="podium-medal">'+medals[idx]+'</span>'
        +(drName(r.plate)?'<div class="podium-plate">'+drName(r.plate)+'</div>'+'<div class="podium-make">'+r.plate+' &middot; '+r.make+(drAge(r.plate)?' &middot; '+drAge(r.plate):'')+'</div>'
        :'<div class="podium-plate">'+r.plate+'</div>'+'<div class="podium-make">'+r.make+'</div>')
        +'<div class="podium-score" style="color:'+sc(r.score)+';">'+fmt1(r.score)+'</div>'
        +'<div style="font-size:10px;color:'+GOLD+';margin-top:2px;">'+stars(r.score)+'</div>'
        +'<div class="podium-trips">'+r.trips+' trip'+(r.trips!==1?'s':'')+'</div>'
        +'<div class="podium-comps">'
          +[['Spd',r.spd],['Brk',r.brk],['Acc',r.acc],['Crn',r.crn]].map(function(p){
            return'<div class="podium-comp"><div class="ck">'+p[0]+'</div>'
              +(p[1]===null?'<div class="cv" style="color:var(--text3);">—</div>':'<div class="cv" style="color:'+sc(p[1])+';">'+p[1]+'</div>')
            +'</div>';
          }).join('')
        +'</div>'
      +'</div>';
    podEl.appendChild(slot);
  });

  const listEl=document.getElementById('lb-list');listEl.innerHTML='';
  if(!wk.rankings.length){listEl.innerHTML='<div class="empty">No scored trips for this week.</div>';return;}
  wk.rankings.forEach(function(r){
    const row=document.createElement('div');
    row.className='lb-row'+(r.rank<=3?' top':'');
    const medal=r.rank===1?'🥇':r.rank===2?'🥈':r.rank===3?'🥉':'';
    row.innerHTML=
      '<div class="rk'+(medal?' medal':'')+'">'+( medal||'#'+r.rank)+'</div>'
      +'<div>'
        +(drName(r.plate)?'<div style="font-size:12px;font-weight:600;">'+drName(r.plate)+ageTag(drAge(r.plate))+'</div>':'') 
        +'<div style="font-size:10px;color:var(--text3);">'+(drName(r.plate)?r.plate+' &middot; ':'')+r.make+' &middot; '+r.trips+' trip'+(r.trips!==1?'s':'')+'</div>'+(r.short||r.std||r.lng?'<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:3px;">'+(r.short?'<span style="font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;background:var(--success-bg);color:var(--success);">'+r.short+' Short</span>':'')+(r.std?'<span style="font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;background:var(--bg3);color:var(--text2);border:0.5px solid var(--border);">'+r.std+' Standard</span>':'')+(r.lng?'<span style="font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;background:var(--info-bg);color:var(--info);">'+r.lng+' Long</span>':'')+'</div>':'')
        +'<div class="bwrap"><div class="bfill" style="width:'+r.score+'%;background:'+sc(r.score)+';"></div></div>'
      +'</div>'
      +'<div class="sc-cell hide-m" style="color:'+sc(r.score)+';">'+fmt1(r.score)+'</div>'
      +'<div class="comp-cell">'+cv(r.spd)+'</div>'
      +'<div class="comp-cell">'+cv(r.brk)+'</div>'
      +'<div class="comp-cell hide-m">'+cv(r.acc)+'</div>'
      +'<div class="comp-cell hide-m">'+cv(r.crn)+'</div>'
      +'<div class="trend-cell">'+trendIcon(r.trend,13)+'</div>';
    listEl.appendChild(row);
  });
  celebrateWinner();
}

// Confetti + trophy pop for the week's #1
function celebrateWinner(){
  if(typeof confetti!=='function')return;
  if(document.getElementById('view-lb').style.display==='none')return; // only when leaderboard is showing
  setTimeout(function(){
    var card=document.querySelector('#podium .podium-card.p1');
    if(!card)return;
    var r=card.getBoundingClientRect();
    var x=(r.left+r.width/2)/window.innerWidth;
    var y=(r.top+r.height*0.35)/window.innerHeight;
    var cols=['#EF9F27','#97c459','#85b7eb','#ef4444','#ffffff','#c084fc'];
    confetti({particleCount:130,spread:75,startVelocity:48,origin:{x:x,y:y},colors:cols,scalar:0.9,ticks:220});
    setTimeout(function(){confetti({particleCount:70,spread:110,startVelocity:32,origin:{x:x,y:y},colors:cols,scalar:0.8});},220);
  },80);
}

// ── Drivers ────────────────────────────────────────────────────────────────
function renderDrivers(){
  var grid = document.getElementById('dr-grid');
  grid.innerHTML = '';
  var drData = loadDrivers();
  var AGES = ['Under 25','25–34','35–44','45–54','55–64','65+'];
  var SEXES = ['Male','Female','Non-binary','Prefer not to say'];
  var head = document.createElement('div');
  head.className = 'dr-head';
  head.innerHTML = '<span>Vehicle</span><span>First name</span><span>Last init.</span><span>Age</span><span>Sex</span><span>Email</span><span class="r">Score</span>';
  grid.appendChild(head);
  vehicles.forEach(function(v){
    var d = drData[v.plate] || {};
    var ageOpts = AGES.map(function(a){ return '<option value="'+a+'"'+(d.age===a?' selected':'')+'>'+a+'</option>'; }).join('');
    var sexOpts = SEXES.map(function(s){ return '<option value="'+s+'"'+(d.sex===s?' selected':'')+'>'+s+'</option>'; }).join('');
    var pid = v.plate.replace(/\s/g,'_');
    var row = document.createElement('div');
    row.className = 'dr-row';
    row.id = 'dr-row-'+pid;
    row.dataset.plate = v.plate;
    row.innerHTML =
      '<div class="dr-id"><div class="p">'+v.plate+'</div><div class="m">'+v.make+'</div></div>'
      +'<input type="text" id="dr-first-'+pid+'" value="'+esc(d.first||'')+'" placeholder="First name">'
      +'<input type="text" id="dr-last-'+pid+'" value="'+esc(d.last||'')+'" placeholder="A" maxlength="1" style="text-transform:uppercase;text-align:center;">'
      +'<select id="dr-age-'+pid+'"><option value="">&#8212;</option>'+ageOpts+'</select>'
      +'<select id="dr-sex-'+pid+'"><option value="">&#8212;</option>'+sexOpts+'</select>'
      +'<input type="email" id="dr-email-'+pid+'" value="'+esc(d.email||'')+'" placeholder="driver@example.com">'
      +'<div class="sc" style="color:'+sc(v.avg)+';">'+fmt1(v.avg)+'</div>';
    grid.appendChild(row);
    var plateRef = v.plate;
    ['first','last','age','sex','email'].forEach(function(field){
      var el = document.getElementById('dr-'+field+'-'+pid);
      if(!el) return;
      el.addEventListener('change', function(){ saveDr(plateRef); });
      el.addEventListener('input',  function(){ saveDr(plateRef); });
    });
  });
  buildAlphaBar();
}
function isValidEmail(v){ return !v || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim()); }
function saveDr(plate){
  var pid = plate.replace(/\s/g,'_');
  var entry={
    first: (document.getElementById('dr-first-'+pid)||{value:''}).value,
    last:  (document.getElementById('dr-last-'+pid)||{value:''}).value,
    age:   (document.getElementById('dr-age-'+pid)||{value:''}).value,
    sex:   (document.getElementById('dr-sex-'+pid)||{value:''}).value,
    email: (document.getElementById('dr-email-'+pid)||{value:''}).value
  };
  var emailEl=document.getElementById('dr-email-'+pid);
  if(!isValidEmail(entry.email)){
    if(emailEl){ emailEl.style.borderColor='var(--danger)'; emailEl.title='Please enter a valid email address'; }
    return;
  }
  if(emailEl){ emailEl.style.borderColor=''; emailEl.title=''; }
  _drCache[plate]=entry;
  _sb.from('drivers').upsert({plate:plate,first_name:entry.first,last_initial:entry.last,age:entry.age,sex:entry.sex,email:entry.email}).then(function(){});
  var row = document.getElementById('dr-row-'+pid);
  if(row){ row.classList.add('saved-flash'); clearTimeout(row._t); row._t=setTimeout(function(){ row.classList.remove('saved-flash'); },900); }
}
function filterDrivers(q){
  q = (q||'').trim().toUpperCase();
  var drData = loadDrivers();
  var cards = document.querySelectorAll('#dr-grid .dr-row');
  var active = document.querySelector('#dr-alpha .alpha-btn.alpha-active');
  if(active && q === active.dataset.l) { /* already set */ }
  // Sync alpha bar highlight
  document.querySelectorAll('#dr-alpha .alpha-btn').forEach(function(btn){
    btn.classList.toggle('alpha-active', btn.dataset.l === q);
    btn.style.background = btn.dataset.l === q ? 'var(--info-bg)' : '';
    btn.style.color = btn.dataset.l === q ? 'var(--info)' : '';
    btn.style.borderColor = btn.dataset.l === q ? 'var(--info)' : '';
  });
  // Sync search input
  var inp = document.getElementById('dr-search');
  if(inp && inp.value.toUpperCase() !== q) inp.value = q;
  cards.forEach(function(card){
    var plate = card.dataset.plate;
    if(!plate) return;
    var d = drData[plate] || {};
    var initial = (d.last||'').trim().toUpperCase()[0] || '';
    card.style.display = (!q || initial === q) ? '' : 'none';
  });
}
function buildAlphaBar(){
  var bar = document.getElementById('dr-alpha');
  if(!bar) return;
  bar.innerHTML = '';
  var drData = loadDrivers();
  var initials = new Set();
  vehicles.forEach(function(v){ var d=drData[v.plate]||{}; var i=(d.last||'').trim().toUpperCase()[0]; if(i) initials.add(i); });
  Array.from(initials).sort().forEach(function(l){
    var btn = document.createElement('button');
    btn.className = 'alpha-btn';
    btn.dataset.l = l;
    btn.textContent = l;
    btn.style.cssText = 'background:var(--bg3);border:0.5px solid var(--border);color:var(--text2);border-radius:5px;width:26px;height:26px;cursor:pointer;font-size:12px;font-weight:500;transition:all .12s;';
    btn.onclick = function(){
      var cur = document.getElementById('dr-search');
      var q = cur && cur.value.toUpperCase() === l ? '' : l;
      filterDrivers(q);
    };
    bar.appendChild(btn);
  });
}
async function exportDrivers(){
  var res=await _sb.from('drivers').select('*');
  var data={};
  if(res.data) res.data.forEach(function(r){ data[r.plate]={first:r.first_name||'',last:r.last_initial||'',age:r.age||'',sex:r.sex||'',email:r.email||''}; });
  var blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
  var a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='driveiq_drivers.json';
  a.click();
  URL.revokeObjectURL(a.href);
}
function importDrivers(evt){
  var file = evt.target.files[0];
  if(!file) return;
  var reader = new FileReader();
  reader.onload = async function(e){
    try{
      var data=JSON.parse(e.target.result);
      // sanitise imported values: strip markup chars, cap length, validate email
      var _clean=function(s){ return String(s==null?'':s).replace(/[<>"]/g,'').slice(0,60); };
      var rows=Object.entries(data).map(function(kv){
        var val=kv[1]||{};
        return {plate:_clean(kv[0]), first_name:_clean(val.first), last_initial:_clean(val.last).slice(0,1).toUpperCase(),
                age:_clean(val.age), sex:_clean(val.sex), email:(isValidEmail(val.email)?_clean(val.email):'')};
      });
      await _sb.from('drivers').upsert(rows);
      _drCache=data;
      renderDrivers();
      renderRanking();
      var btn=evt.target.parentElement;
      var orig=btn.innerHTML;
      btn.innerHTML='<i class="ti ti-check" style="font-size:13px;color:var(--success);"></i> Imported';
      setTimeout(function(){ btn.innerHTML=orig; },2000);
    }catch(err){ alert('Invalid driver file.'); }
  };
  reader.readAsText(file);
  evt.target.value = '';
}
