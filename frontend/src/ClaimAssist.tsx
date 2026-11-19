import {useState} from 'react';

type Any = Record<string, any>;
const SL_CLAIM_URL='https://sl.se/kundservice/forseningsersattning';

export function ClaimAssist({check}: {check:Any}) {
 const journey=check.journey, delay=check.delay;
 const arrivalDelay=journey?.scheduled_arrival&&delay?.estimated_arrival
  ? Math.max(0,Math.round((new Date(delay.estimated_arrival).getTime()-new Date(journey.scheduled_arrival).getTime())/60000))
  : 0;
 const [cost,setCost]=useState(''),[operator,setOperator]=useState(''),[travelDate,setTravelDate]=useState((journey?.scheduled_departure||check.created_at||'').slice(0,10)),[receipt,setReceipt]=useState(''),[copied,setCopied]=useState(false);
 const demoRide=check.ride?.status==='simulated';
 const copySummary=async()=>{
  const summary=[
   'SL delay claim notes (review and submit directly to SL)',
   `SL route: ${journey?.route_name||'SL journey'}`,
   `Planned departure: ${journey?.scheduled_departure||''} · Planned arrival: ${journey?.scheduled_arrival||''}`,
   `Estimated arrival: ${delay?.estimated_arrival||''}`,
   `Estimated arrival delay: ${arrivalDelay} minutes`,
   `Actual alternative transport: ${operator||'Add operator'}`,
   `Travel date: ${travelDate||'Add date'} · Actual cost: ${cost?`${cost} SEK`:'Add cost'}`,
   `Receipt selected in this browser: ${receipt||'No file selected'}`,
   demoRide?'The app ride is simulated and is not evidence of a real purchase.':'',
  ].filter(Boolean).join('\n');
  try{await navigator.clipboard.writeText(summary);setCopied(true);setTimeout(()=>setCopied(false),2000)}catch{setCopied(false)}
 };
 return <section className="claim-assist" aria-label="SL claim assistance">
  <div className="claim-head"><div><small>CLAIM ASSISTANCE</small><h3>Prepare an SL delay claim</h3></div><span className={arrivalDelay>20?'claim-possible':'claim-check'}>{arrivalDelay>20?'Check possible eligibility':'Review SL conditions'}</span></div>
  <p className="claim-guidance">This check estimates your arrival delay at <b>{arrivalDelay} min</b>. SL says alternative-travel compensation may apply when you risk being more than 20 minutes late. SL reviews each claim and decides eligibility.</p>
  {demoRide&&<p className="claim-warning"><b>Demo ride is simulated.</b> It was not an Uber trip and its details are not a receipt. Only enter costs and attach a receipt for an alternative ride you actually took.</p>}
  <div className="claim-grid">
   <label>Actual transport operator<input value={operator} onChange={e=>setOperator(e.target.value)} placeholder="e.g. Uber"/></label>
   <label>Actual trip date<input type="date" value={travelDate} onChange={e=>setTravelDate(e.target.value)}/></label>
   <label>Actual amount paid (SEK)<input type="number" min="0" step="0.01" value={cost} onChange={e=>setCost(e.target.value)} placeholder="0.00"/></label>
   <label>Receipt reminder<input type="file" accept="application/pdf,image/jpeg,image/png" onChange={e=>setReceipt(e.target.files?.[0]?.name||'')}/><small>The file stays in this browser. Attach the original receipt on SL’s form.</small></label>
  </div>
  <p className="claim-note">SL’s current guidance lists compensation up to 1,480 SEK for eligible alternative travel and requires claims within 3 months. A receipt is required. This app does not send or submit your claim.</p>
  <div className="claim-actions"><button type="button" className="secondary" onClick={copySummary}>{copied?'Copied claim notes':'Copy claim notes'}</button><a className="primary" href={SL_CLAIM_URL} target="_blank" rel="noopener noreferrer">Open SL claim information ↗</a></div>
 </section>;
}
