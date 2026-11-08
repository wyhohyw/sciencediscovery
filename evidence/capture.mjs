// Real, isolated product evidence. No mock services or product source changes.
import assert from 'node:assert/strict';
import {execFileSync,spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {createServer} from 'node:http';
import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const directory=resolve('.tmp','pr770-real-run'), evidence=resolve('.tmp','pr770-real-evidence-output');
await mkdir(directory,{recursive:true});await mkdir(evidence,{recursive:true});
const token=randomUUID(),graphToken=randomUUID(),dbPassword='Ephemeral-'+randomUUID();
const passwords=[dbPassword],steps=[],captures=[],ordinaryLeaks=[],proofs=[];
const container='pr770-neo4j-'+randomUUID();
for(const secret of [token,graphToken,dbPassword]) console.log('::add-mask::'+secret);
const redact=value=>[token,graphToken,dbPassword,Buffer.from('neo4j:'+dbPassword).toString('base64')].reduce((s,v)=>s.replaceAll(v,'[redacted]'),String(value));
const git=(...args)=>execFileSync('git',args,{encoding:'utf8'}).trim();
const sha=git('rev-parse','HEAD'),tree=git('rev-parse','HEAD^{tree}');
let origin,graphOrigin,product,graphProcess,browser,context,project,session,apiEnv,graphEnv,outcome='FAIL',logs='',dockerStarted=false;
const initialOrigin='http://localhost:7474',neo4jOrigin='http://127.0.0.1:7474',expectedUser='neo4j';
async function reserve(){const s=createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
async function api(path,body,method=body===undefined?'GET':'POST',expected=body===undefined?200:201){const response=await fetch(origin+path,{method,redirect:'error',signal:AbortSignal.timeout(30000),headers:{authorization:`Bearer ${token}`,'content-type':'application/json','Idempotency-Key':randomUUID()},...(body===undefined?{}:{body:JSON.stringify(body)})});const value=await response.json();assert.equal(response.status,expected,`${method} ${path}: ${value.code??response.status}`);assert.ok(!JSON.stringify(value).includes(dbPassword),'Secret reflected in public response');return value;}
const formal=()=>api('/api/memory/settings');
const path=(card,action)=>`/api/sessions/${session.id}/configuration-requests${card?'/'+card.id:''}${action?'/'+action:''}`;
async function step(name,work){try{await work();steps.push({name,status:'PASS'});}catch(e){steps.push({name,status:'FAIL',error:redact(e.message)});throw e;}}
async function poll(work,label,ms=90000){let last;const until=Date.now()+ms;do{try{return await work();}catch(e){last=e;}await new Promise(r=>setTimeout(r,400));}while(Date.now()<until);throw new Error(label+': '+redact(last?.message));}
function launch(command,args,env){const child=spawn(command,args,{env,stdio:['ignore','pipe','pipe']});for(const st of [child.stdout,child.stderr])st.on('data',b=>{logs+=redact(b);});return child;}
async function stop(child){if(child&&child.exitCode===null&&child.signalCode===null){child.kill('SIGTERM');await Promise.race([new Promise(r=>child.once('exit',r)),new Promise(r=>setTimeout(r,8000))]);if(child.exitCode===null&&child.signalCode===null)throw new Error('Owned process did not exit');}}
async function graphHealth(){const r=await fetch(graphOrigin+'/health',{signal:AbortSignal.timeout(15000)});assert.equal(r.status,200);return r.json();}
async function startGraph(){graphProcess=launch(process.env.EVIDENCE_PYTHON??'python3',['-m','sciencediscovery_memory_graph.server'],graphEnv);await poll(async()=>{assert.equal(graphProcess.exitCode,null);return graphHealth();},'Real MemoryGraph startup');}
async function startProduct(){product=launch(process.execPath,['services/api/dist/server.js'],apiEnv);await poll(async()=>{assert.equal(product.exitCode,null);return api('/health');},'Actual product startup');}
async function dbRead(){const r=await fetch(neo4jOrigin+'/db/neo4j/tx/commit',{method:'POST',headers:{authorization:'Basic '+Buffer.from('neo4j:'+dbPassword).toString('base64'),'content-type':'application/json'},body:JSON.stringify({statements:[{statement:'RETURN 1 AS authenticated_read'}]}),signal:AbortSignal.timeout(15000)});assert.equal(r.status,200);const d=await r.json();assert.equal(d.errors.length,0);assert.equal(d.results[0].data[0].row[0],1);return true;}
try{
 await step('1. Verify exact product and start official authenticated Neo4j Community',async()=>{
  assert.equal(sha,'595ea493ab62dda8372f3257fb54fc5f2f1b738d');assert.equal(tree,'3aa1eea1da91fa30ef1e703285d2e3463b5feedb');assert.equal(git('status','--porcelain','--untracked-files=no'),'');
  execFileSync('docker',['run','--detach','--rm','--name',container,'--publish','127.0.0.1:7474:7474','--env','NEO4J_AUTH','neo4j:5.26-community'],{env:{...process.env,NEO4J_AUTH:'neo4j/'+dbPassword},stdio:'pipe'});dockerStarted=true;
  await poll(dbRead,'Neo4j authenticated read',180000);
  const d=JSON.parse(execFileSync('docker',['image','inspect','neo4j:5.26-community','--format','{{json .RepoDigests}}'],{encoding:'utf8'}));proofs.push({stage:'initial',officialImage:'neo4j:5.26-community',imageDigests:d,authenticatedNeo4jRead:true});
 });
 await step('2. Start actual repository MemoryGraph and API on owned loopback ports',async()=>{
  const port=await reserve(),graphPort=await reserve();origin=`http://127.0.0.1:${port}`;graphOrigin=`http://127.0.0.1:${graphPort}`;
  const clean={...process.env};for(const k of ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy'])clean[k]='';
  graphEnv={...clean,SCIENCE_AGENT_MEMORY_GRAPH_HOST:'127.0.0.1',SCIENCE_AGENT_MEMORY_GRAPH_PORT:String(graphPort),SCIENCE_AGENT_MEMORY_GRAPH_DATA_DIR:resolve(directory,'memory-graph'),SCIENCE_AGENT_MEMORY_GRAPH_BACKEND:'local',SCIENCE_AGENT_MEMORY_GRAPH_INTERNAL_TOKEN:graphToken,SCIENCE_AGENT_MEMORY_GRAPH_NEO4J_HTTP:initialOrigin,SCIENCE_AGENT_MEMORY_GRAPH_NEO4J_USER:'neo4j',SCIENCE_AGENT_DATA_DIR:resolve(directory,'data')};
  apiEnv={...clean,SCIENCE_AGENT_HOST:'127.0.0.1',SCIENCE_AGENT_PORT:String(port),SCIENCE_AGENT_AUTH_TOKEN:token,SCIENCE_AGENT_DATA_DIR:resolve(directory,'data'),SCIENCE_DISCOVERY_DATA_DIR:resolve(directory,'data'),SCIENCE_AGENT_SAMPLE_SESSION:'0',SCIENCE_AGENT_EXECUTOR:'native',SCIENCE_AGENT_MEMORY_GRAPH_AVAILABLE:'1',SCIENCE_AGENT_MEMORY_GRAPH_URL:graphOrigin,SCIENCE_AGENT_MEMORY_GRAPH_INTERNAL_TOKEN:graphToken,SCIENCE_AGENT_RUNNER_URL:'http://127.0.0.1:1'};
  await startGraph();await startProduct();assert.equal((await graphHealth()).backend,'local');project=await api('/api/projects',{name:'Real Neo4j configuration evidence'});
 });
 execFileSync(process.execPath,['test/sync-e2e.mjs','--check']);const {chromium}=createRequire(resolve('.e2e/package.json'))('@playwright/test');browser=await chromium.launch({headless:true,channel:'chromium'});
  for (const locale of ["en", "zh-CN"]) {
    context = await browser.newContext({ viewport: { width: 1440, height: 2600 }, deviceScaleFactor: 1 });
    await context.route("**/*", route => { const url = new URL(route.request().url()); return ["http:", "https:"].includes(url.protocol) && url.origin !== origin ? route.abort() : route.continue(); });
    await context.addInitScript(({ token, origin, locale }) => { if (location.origin === origin) { localStorage.setItem("sciencediscovery-token", token); localStorage.setItem("sciencediscovery-locale", locale); } }, { token, origin, locale });
    const m = locale === "en" ? { allow: "Allow this exact endpoint", preview: "Refresh proposed changes", confirm: "Confirm choice and continue" } : { allow: "允许此精确端点", preview: "刷新修改方案", confirm: "确认选择并继续" };
    const page = await context.newPage();
    page.on("request", request => { if (request.method() !== "POST") return; const body = request.postData() || ""; if (!request.url().endsWith("/credentials") && passwords.some(secret => body.includes(JSON.stringify(secret).slice(1,-1)))) ordinaryLeaks.push(true); });
    async function capture(name, locator) {
      await locator.scrollIntoViewIfNeeded(); await page.waitForTimeout(250);
      assert.ok(passwords.every(secret => !(awaitText.includes(secret.trim()))));
      const filename = resolve(evidence, `${locale}-${name}.png`);
      await locator.screenshot({ path: filename, animations: "disabled", caret: "hide" });
      captures.push({ locale, name, filename, text: await locator.innerText() });
    }
    let awaitText = "";
    async function snap(name, locator) { awaitText = await page.locator("body").innerText(); await capture(name, locator); }
    async function open(capabilityId, values) {
      session = await api(`/api/projects/${project.id}/sessions`, { title: `${locale} ${capabilityId} evidence` });
      const card = await api(path(), { protocolVersion: 3, capabilityId, values });
      await page.goto(`${origin}/projects/${project.id}/sessions/${session.id}`, { waitUntil: "domcontentloaded" });
      const panel = page.locator(`#configuration-request-${card.id}`); await panel.waitFor({ state: "visible" }); return panel;
    }
    await step(`${locale}: 2. Credential destination consent, masked input, write-only preview, manual save and restart notice`, async () => {
      await api("/api/memory/settings", { enabled: false, backend: "neo4j", neo4jHttp: initialOrigin, neo4jUser: expectedUser, neo4jPassword: null }, "PUT", 200);
      const panel = await open("memory.credential", { operation: "replace" });
      await snap("credential-before-consent", panel);
      assert.equal(await panel.locator('input[type="password"]').count(), 0);
      await panel.getByRole("button", { name: m.allow, exact: true }).click();
      const password = panel.locator('input[type="password"]'); await password.waitFor({state:"visible"});
      await password.fill(dbPassword);
      await snap("credential-masked-input", panel);
      await panel.getByRole("button", { name: m.preview, exact: true }).click();
      await page.waitForFunction(() => { const e = document.querySelector('input[type="password"]'); return e && e.value === ""; });
      assert.equal((await formal()).hasNeo4jPassword, false);
      assert.equal(await panel.getByRole("button", { name: m.confirm, exact: true }).isEnabled(), true);
      await snap("credential-approved-manual-save-needed", panel);
      await panel.getByRole("button", { name: m.confirm, exact: true }).click();
      await panel.waitFor({state:"detached"}); assert.equal((await formal()).hasNeo4jPassword, true);
      const record = page.locator(".conversation-configuration-record").last(); await record.locator("summary").first().click();
      await snap("credential-saved-restart-required", record);
    });
    await step(`${locale}: 3. Connection recipient before and after consent, manual save and restart notice`, async () => {
      const panel = await open("memory.connection", { neo4jHttp: neo4jOrigin, neo4jUser: expectedUser });
      await snap("connection-before-consent", panel);
      assert.equal(await panel.getByRole("button", {name:m.confirm, exact:true}).isEnabled(), false);
      await panel.getByRole("button", {name:m.allow, exact:true}).click();
      await panel.getByRole("button", {name:m.confirm, exact:true}).waitFor();
      assert.equal((await formal()).neo4jHttp, initialOrigin);
      await snap("connection-approved-manual-save-needed", panel);
      await panel.getByRole("button", {name:m.confirm, exact:true}).click(); await panel.waitFor({state:"detached"});
      assert.equal((await formal()).neo4jHttp, neo4jOrigin);
      const record = page.locator(".conversation-configuration-record").last(); await record.locator("summary").first().click();
      await snap("connection-saved-restart-required", record);
    });
    await step(`${locale}: 4. Backend switch and honest readiness status`, async () => {
      await api("/api/memory/settings", { enabled: false, backend: "local" }, "PUT", 200);
      const panel = await open("memory.backend", { backend: "neo4j" });
      await snap("backend-before-save", panel);
      await panel.getByRole("button", {name:m.confirm, exact:true}).click(); await panel.waitFor({state:"detached"});
      assert.equal((await formal()).backend, "neo4j");
      const record = page.locator(".conversation-configuration-record").last(); await record.locator("summary").first().click();
      await snap("backend-saved-readiness-unverified", record);
    });
    await step(`${locale}: 5. Enable real memory participation without treating save as readiness proof`, async () => {
      const panel = await open("memory.enable", { enabled: true });
      await snap("enable-before-save", panel);
      await panel.getByRole("button", {name:m.confirm, exact:true}).click(); await panel.waitFor({state:"detached"});
      const record = page.locator(".conversation-configuration-record").last(); await record.locator("summary").first().click();
      await snap("enable-saved-readiness-unverified", record);
    });
    await step(`${locale}: 6. Explicitly restart both actual services and verify Neo4j-backed reads`, async () => {
      await stop(product); await stop(graphProcess); await startGraph(); await startProduct();
      const settings = await formal(); assert.equal(settings.enabled,true); assert.equal(settings.backend,"neo4j");assert.equal(settings.neo4jHttp,neo4jOrigin);assert.equal(settings.neo4jUser,"neo4j");assert.equal(settings.hasNeo4jPassword,true);
      const health=await poll(async()=>{const h=await graphHealth();assert.equal(h.status,"healthy");assert.equal(h.backend,"neo4j");return h;},"Real Neo4j backend after restart");
      await dbRead();const graph=await api(`/api/memory/subgraph?session_id=${session.id}`);assert.ok(Array.isArray(graph.nodes));assert.ok(Array.isArray(graph.edges));
      proofs.push({locale,afterExplicitRestart:true,memoryGraph:health,authenticatedNeo4jRead:true,productSubgraphRead:true,settings:{enabled:settings.enabled,backend:settings.backend,neo4jHttp:settings.neo4jHttp,neo4jUser:settings.neo4jUser,hasNeo4jPassword:settings.hasNeo4jPassword}});
    });
    await context.close(); context = undefined;
  }
  assert.equal(ordinaryLeaks.length,0); outcome="PASS";
  await writeFile(resolve(evidence,"captures.json"),JSON.stringify({sha,tree,chromium:await browser.version(),viewport:{width:1440,height:2600},deviceScaleFactor:1,ordinaryLeaks:0,boundary:"Actual product, actual repository MemoryGraph, official authenticated Neo4j Community. No mock services. Fresh ephemeral data and runtime-generated credentials. No model calls. Saved cards captured before explicit operator restart; readiness checked separately afterward.",captures},null,2));
}catch(e){console.error(redact(e.stack));process.exitCode=1;}
finally{
 await context?.close();await browser?.close();
 try{if(project)await api(`/api/projects/${project.id}`,{confirmationId:project.id},"DELETE",200);}catch(e){steps.push({name:"Owned project cleanup",status:"FAIL",error:redact(e.message)});outcome="FAIL";process.exitCode=1;}
 for(const child of [product,graphProcess])try{await stop(child);}catch(e){outcome="FAIL";process.exitCode=1;}
 if(dockerStarted)try{execFileSync("docker",["stop",container],{stdio:"pipe"});}catch(e){outcome="FAIL";process.exitCode=1;}
 await writeFile(resolve(directory,"private-redacted-startup.log"),logs);
 await writeFile(resolve(evidence,"result.json"),JSON.stringify({outcome,sha,tree,steps,proofs,ordinaryLeaks:ordinaryLeaks.length,captureCount:captures.length,boundary:"Real MemoryGraph and authenticated official Neo4j Community; isolated ephemeral data; no model or production service",ownedServicesStopped:true},null,2));
 console.log(JSON.stringify({outcome,captureCount:captures.length,sha}));
}
