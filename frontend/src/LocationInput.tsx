import React, {useEffect, useRef, useState} from 'react';

type Coordinates = {latitude:number; longitude:number};
type Place = {name:string; place_id?:string; provider?:string; coordinates?:Coordinates};
const sessionToken=()=>globalThis.crypto?.randomUUID?.() || Date.now().toString(36)+Math.random().toString(36).slice(2);
type Props = {label:string; id:string; value:string; placeId?:string; coordinates?:Coordinates; google:boolean;
 onChange:(value:string, placeId?:string, coordinates?:Coordinates)=>void};

export function LocationInput({label,id,value,placeId,coordinates,google,onChange}:Props){
 const [items,setItems]=useState<Place[]>([]),[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[active,setActive]=useState(-1),[searched,setSearched]=useState(false);
 const token=useRef(sessionToken()),version=useRef(0);
 const request=async(path:string,signal?:AbortSignal)=>{
  const response=await fetch('/api/locations'+path,{signal});
  if(response.status===401)window.dispatchEvent(new Event('session-expired'));
  const data=await response.json();
  if(!response.ok)throw new Error(data.detail||'Location search is unavailable.');
  return data;
 };
 useEffect(()=>{
  const controller=new AbortController();
  setItems([]);setActive(-1);setSearched(false);setError('');
  if(!open||placeId||coordinates||value.trim().length<2){setBusy(false);return;}
  setBusy(true);
  const timer=setTimeout(async()=>{
   try{const result=await request('?q='+encodeURIComponent(value)+'&session_token='+token.current,controller.signal);if(!controller.signal.aborted){setItems(result);setSearched(true)}}
   catch(e){if(!controller.signal.aborted)setError((e as Error).message)}
   finally{if(!controller.signal.aborted)setBusy(false)}
  },300);
  return()=>{clearTimeout(timer);controller.abort()};
 },[value,placeId,coordinates,open]);
 const select=async(place:Place)=>{
  const current=++version.current;
  if(!place.place_id){onChange(place.name,undefined,place.coordinates);setOpen(false);return;}
  setBusy(true);setError('');
  try{
   const detail=await request('/details?place_id='+encodeURIComponent(place.place_id)+'&session_token='+token.current);
   if(version.current===current){onChange(place.name,detail.place_id);setOpen(false);token.current=sessionToken()}
  }catch(e){if(version.current===current)setError((e as Error).message)}
  finally{if(version.current===current)setBusy(false)}
 };
 return <div className="location-field"><label htmlFor={id}>{label}</label><input id={id} required maxLength={200} autoComplete="off" role="combobox" aria-autocomplete="list" aria-expanded={open&&items.length>0} aria-controls={id+'-list'} aria-activedescendant={active>=0?id+'-option-'+active:undefined} aria-describedby={id+'-help'} value={value} placeholder="Search an address, station or landmark" onFocus={()=>setOpen(true)} onBlur={()=>{version.current++;setOpen(false)}} onChange={e=>{version.current++;onChange(e.target.value);setOpen(true)}} onKeyDown={e=>{
  if(e.key==='Escape'){setOpen(false);return;}
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setOpen(true);setActive(i=>items.length?(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length:-1)}
  if(e.key==='Enter'&&open&&active>=0&&items[active]){e.preventDefault();select(items[active])}
 }}/><small id={id+'-help'} className="location-help">{placeId||coordinates?'Place selected':'Choose a suggestion to use an address.'}</small>
 {open&&<><div role="status" className="location-feedback">{busy?'Searching…':error||(!placeId&&!coordinates&&searched&&items.length===0?'No matching places. Try a more specific address.':'')}</div>{items.length>0&&<div className="location-results"><ul id={id+'-list'} role="listbox" aria-label={label+' suggestions'}>{items.map((place,index)=><li role="option" aria-selected={active===index} id={id+'-option-'+index} key={place.place_id||place.name+JSON.stringify(place.coordinates)} onPointerDown={e=>e.preventDefault()} onClick={()=>select(place)}><span aria-hidden="true">⌖</span>{place.name}</li>)}</ul><div className="location-attribution">{google?'Google Maps':<>© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a> · Photon</>}</div></div>}</>}
 </div>;
}
