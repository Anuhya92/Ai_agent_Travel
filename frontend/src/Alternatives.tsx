import React from 'react';

type Plan = {
 source:string; ai_status:string; reason:string; origin:string; destination:string;
 departure:string; notice:string;
 options:{operator:string; mode:string; reason:string; url:string}[];
};

export function Alternatives({plan}:{plan:Plan}){
 return <section className="alternatives" aria-label="Alternative transport suggestions">
  <h3>Alternative ways to travel</h3>
  <p>{plan.reason}</p>
  <p><b>{plan.origin} → {plan.destination}</b><br/>Requested departure: {new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Stockholm',dateStyle:'medium',timeStyle:'short'}).format(new Date(plan.departure))} (Stockholm)</p>
  <div className="alternative-source">{plan.source==='gemini'?'Suggested by Gemini · availability unverified':plan.ai_status==='unavailable'?'Gemini is unavailable · operator search links': 'Operator search links · Gemini is not configured'}</div>
  {plan.options.map(option=><article key={option.operator} className="alternative-option"><strong>{option.operator} <small>{option.mode}</small></strong><p>{option.reason}</p><a href={option.url} target="_blank" rel="noopener noreferrer">Check routes on {option.operator} ↗</a></article>)}
  <p className="alternative-notice">{plan.notice}</p>
 </section>;
}
