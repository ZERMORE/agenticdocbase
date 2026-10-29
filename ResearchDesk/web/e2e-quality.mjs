// Real browser + local doc2md/DocBase, no external model requests.
import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
const report={model_api_requests:0,steps:[],errors};
const out=new URL('../design/quality-v1/',import.meta.url);await fs.mkdir(out,{recursive:true});
await page.goto('http://127.0.0.1:8765');
await page.getByRole('button',{name:'知识库',exact:true}).click();
await page.getByRole('button',{name:'新建知识库',exact:true}).click();
await page.getByRole('textbox',{name:'名称',exact:true}).fill('质量版验收 · 离线');
await page.getByRole('dialog').getByRole('button',{name:'保存',exact:true}).click();
await page.getByRole('heading',{name:'质量版验收 · 离线',exact:true}).waitFor();
await page.getByRole('button',{name:'上传资料',exact:true}).click();
await page.locator('input[type=file]').setInputFiles(fileURLToPath(new URL('../../doc2md/examples/sample.pdf',import.meta.url)));
await page.getByRole('dialog').waitFor({state:'hidden'});
await page.locator('.document-row:not(.table-head)').filter({hasText:'sample.pdf'}).getByText('可检索',{exact:true}).waitFor({timeout:120000});
await page.getByRole('button',{name:/sample.pdf/}).click();
await page.getByRole('button',{name:'质量与修订',exact:true}).click();
await page.getByRole('heading',{name:'解析质量与修订'}).waitFor();
await page.getByText('个待处理块',{exact:false}).waitFor();
report.steps.push('真实PDF离线解析、入库、页级质量报告');
await page.screenshot({path:fileURLToPath(new URL('quality-1440.png',out)),fullPage:true});
const select=page.getByRole('combobox',{name:'选择修订块'});
const value=await select.locator('option').filter({hasText:'1729'}).first().getAttribute('value');
await select.selectOption(value);
const old=await page.getByRole('textbox',{name:'修正文字',exact:true}).inputValue();
await page.getByRole('textbox',{name:'修正文字',exact:true}).fill(old.replaceAll('1729','1730'));
await page.getByRole('textbox',{name:'修订原因',exact:true}).fill('自动化离线验收，仅修改测试库的派生产物');
await page.getByRole('button',{name:'保存文字纠错',exact:true}).click();
await page.getByText('修订已排队。',{exact:false}).waitFor();
await page.getByRole('button',{name:'关闭',exact:true}).click();
// Use same-origin API to wait for the persistent worker and inspect the actual published revision.
const kb=await page.evaluate(async()=>{const rows=await (await fetch('/api/knowledge-bases')).json();return rows.find(x=>x.name==='质量版验收 · 离线');});
async function waitReady(){
  for(let n=0;n<240;n++){
    const state=await page.evaluate(async id=>{
      const jobs=await(await fetch('/api/jobs')).json();const libs=await(await fetch('/api/knowledge-bases')).json();
      return {jobs:jobs.filter(x=>x.kb===id),kb:libs.find(x=>x.id===id)};
    },kb.id);
    const revise=state.jobs.find(x=>x.kind==='revise');
    if(revise?.state==='failed')throw new Error(revise.error);
    if(revise?.state==='succeeded'&&state.kb.generation===state.kb.published_generation)return;
    await page.waitForTimeout(500);
  }throw new Error('Revision/index did not finish');
}
await waitReady();
await page.getByRole('button',{name:/sample.pdf/}).click();
await page.getByRole('button',{name:'Markdown',exact:true}).click();
await page.locator('.markdown-preview').getByText(/1730/).waitFor();
await page.getByRole('button',{name:'质量与修订',exact:true}).click();
await page.getByRole('combobox',{name:'选择修订块'}).locator('option').filter({hasText:'1730'}).waitFor({state:'attached'});
report.steps.push('网页文字纠错→派生产物→重新建库→新Markdown内容');
await page.screenshot({path:fileURLToPath(new URL('corrected-1440.png',out)),fullPage:true});
for(const width of [1024,390]){
  await page.setViewportSize({width,height:900});
  await page.screenshot({path:fileURLToPath(new URL(`quality-${width}.png`,out)),fullPage:true});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Overflow at '+width);
}
await page.getByRole('button',{name:'关闭',exact:true}).click();
await page.setViewportSize({width:1440,height:1000});
page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'回收',exact:true}).click();
await page.getByRole('button',{name:'个性化设置',exact:true}).click();
await page.getByRole('heading',{name:'备份与恢复',exact:true}).waitFor();
await page.screenshot({path:fileURLToPath(new URL('operations-1440.png',out)),fullPage:true});
report.steps.push('测试库回收、自动备份设置可见');
const history=page.locator('.history-list>button').filter({hasText:'CLIP-Adapter'}).first();
if(await history.count()){
  await history.click();await page.locator('.citation').first().click();
  await page.locator('.evidence-location img').waitFor({timeout:60000});
  await page.screenshot({path:fileURLToPath(new URL('locator-1440.png',out)),fullPage:true});
  report.steps.push('历史回答引用→真实PDF页定位渲染，未请求模型');
}
await fs.writeFile(new URL('browser-report.json',out),JSON.stringify(report,null,2));
await browser.close();
if(errors.length)throw new Error(errors.join('\n'));
console.log(JSON.stringify(report,null,2));
