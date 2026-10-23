import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
// The header tagline only jokes about sources this install has connected.
const html=await readFile(new URL('./dashboard.html',import.meta.url),'utf8');
const block=html.slice(html.indexOf('// taglines:start'),html.indexOf('// taglines:end'));
const {TAGLINES,taglinePool}=new Function(block+';return {TAGLINES,taglinePool}')();
const generic=TAGLINES.filter(([,s,w])=>!s&&!w).map(([t])=>t);
const FRI=new Date(2026,9,23,10),MON=new Date(2026,9,26,10);
assert.ok(generic.length>=15);
for(const [text,src,when] of TAGLINES){assert.ok(!/[—–]/.test(text),text);if(src)assert.ok(Array.isArray(src)&&src.length,text);if(when)assert.ok(when.days.length&&when.days.every(d=>d>=1&&d<=7),text)}
// Not loaded yet, or nothing connected: generic only, so no wrong joke flashes.
assert.deepEqual(taglinePool(TAGLINES,undefined,FRI),generic);
assert.deepEqual(taglinePool(TAGLINES,[],FRI),generic);
// A Monday-only line never shows on Friday, and can on Monday.
const monday='Making Monday slightly less Monday.';
assert.ok(!taglinePool(TAGLINES,[],FRI).includes(monday));
assert.ok(taglinePool(TAGLINES,[],MON).includes(monday));
assert.ok(!taglinePool(TAGLINES,[],new Date(2026,9,25,10)).includes(monday));
const slack=taglinePool(TAGLINES,[{service:'slack'}],FRI);
assert.ok(slack.includes('I’ve seen your Slack. We should talk.'));
assert.ok(!slack.includes('I found the email you were avoiding.'));
assert.equal(slack.length,generic.length+TAGLINES.filter(([,s])=>s?.includes('slack')).length);
const mail=taglinePool(TAGLINES,[{service:'gmail'},{service:'calendar'}],FRI);
assert.ok(mail.includes('I found the email you were avoiding.')&&mail.includes('Your calendar called. I took notes.'));
assert.ok(!mail.some(t=>/Slack/.test(t)));
// Outlook gets the email and calendar lines too.
const ms=taglinePool(TAGLINES,[{service:'outlook'},{service:'outlook_calendar'}],FRI);
assert.ok(ms.includes('I read your email so you don’t have to.')&&ms.includes('Your inbox, but with opinions.')&&ms.includes('Your calendar called. I took notes.'));
// Every tagged source is one the dashboard knows.
const known=new Set(['gmail','outlook','calendar','outlook_calendar','slack','salesflare','whatsapp','linkedin','android','github','stripe','rss','x','trello']);
for(const [,src] of TAGLINES)for(const s of src||[])assert.ok(known.has(s),s);
console.log('taglines ok');
