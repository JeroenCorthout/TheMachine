// Clicks a sidebar row the way a person would: below 1024px the sidebar is a drawer, so it opens the drawer first when
// the row is out of sight (picking a tab, a card or Settings closes it again). It waits for the drawer to stop sliding.
const still=page=>page.waitForFunction(()=>!document.querySelector('#app-side')?.getAnimations().length);
export async function navClick(row){const page=row.page();await still(page);
 if(!(await row.isVisible())&&await page.evaluate(()=>typeof drawerOpen==='function'&&!matchMedia('(min-width:1024px)').matches)){
  await page.evaluate(()=>drawerOpen());await row.waitFor({state:'visible'});await still(page)}
 await row.click();await still(page)}
// The card filter (All, Work, Personal) in the sidebar; on a phone the drawer closes again afterwards
export async function scopeClick(page,name){await navClick(page.locator(`#side-cards [data-scope="${name.toLowerCase()}"]`));
 if(await page.evaluate(()=>document.documentElement.dataset.drawer==='open')){await page.keyboard.press('Escape');await still(page)}}
