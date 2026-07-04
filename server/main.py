"""
K10 Compile Server
==================
异步编译服务：上传 → 返回 build_id → 轮询状态 → 下载 .bin。
解决长耗时编译导致网络超时的问题。
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# ── 版本 ──────────────────────────────────────────────────
VERSION = "3.1.0"

# ── 配置 ──────────────────────────────────────────────────
HOST = os.getenv("K10_COMPILE_HOST", "0.0.0.0")
PORT = int(os.getenv("K10_COMPILE_PORT", "8900"))
UPLOAD_SIZE_LIMIT = 10 * 1024 * 1024
COMPILE_TIMEOUT = int(os.getenv("K10_COMPILE_TIMEOUT", "300"))
MAX_CONCURRENT_COMPILES = int(os.getenv("K10_MAX_CONCURRENT", "2"))
MAX_LOG_LENGTH = 50000
BUILD_RESULT_TTL = int(os.getenv("K10_BUILD_TTL", "1800"))
CLEANUP_INTERVAL = 120
FLASH_PORT = os.getenv("K10_FLASH_PORT", "")  # e.g. /dev/ttyUSB0
REDIRECT_PORT = int(os.getenv("K10_REDIRECT_PORT", "8080"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("k10-compile")

app = FastAPI(title="K10 Compile Server", version=VERSION)

# 静态文件（esptool-js 等）
STATIC_DIR = os.environ.get("K10_STATIC_DIR", os.path.join(os.path.dirname(__file__), "static"))
if os.path.isdir(STATIC_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# npm 依赖（esptool-js target 文件）
NPM_DIR = os.environ.get("K10_NPM_DIR", os.path.join(os.path.dirname(__file__), "npm"))
if os.path.isdir(NPM_DIR):
    from fastapi.staticfiles import StaticFiles
    app.mount("/npm", StaticFiles(directory=NPM_DIR), name="npm")

_start_time = time.time()
_compile_semaphore = asyncio.Semaphore(MAX_CONCURRENT_COMPILES)
_waiting_count = 0
_build_results: dict[str, dict] = {}


def new_build_result() -> dict:
    return {
        "status": "queued", "progress": 0, "log": "",
        "bin_path": None, "bin_size": 0, "error": None,
        "created_at": time.time(), "finished_at": None,
    }


WEB_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title id="pageTitle">K10 固件编译服务</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f0f2f5;color:#333;display:flex;flex-direction:column;align-items:center}
  .container{max-width:720px;width:100%;padding:24px 16px}
  h1{font-size:24px;font-weight:600;margin-bottom:4px}
  .subtitle{color:#666;font-size:14px;margin-bottom:24px}
  .lang-switch{position:absolute;top:16px;right:16px;display:flex;gap:4px}
  .lang-switch button{background:#fff;border:1px solid #d9d9d9;border-radius:4px;padding:4px 10px;font-size:12px;cursor:pointer;color:#555;transition:all .15s}
  .lang-switch button:hover{border-color:#4A90D9;color:#4A90D9}
  .lang-switch button.active{background:#4A90D9;color:#fff;border-color:#4A90D9}
  .drop-zone{border:2px dashed #ccc;border-radius:12px;padding:32px 20px;text-align:center;cursor:pointer;background:#fff;position:relative;transition:all .2s}
  .drop-zone:hover,.drop-zone.dragover{border-color:#4A90D9;background:#f0f7ff}
  .drop-zone-icon{font-size:40px;margin-bottom:8px}
  .drop-zone-text{font-size:15px;color:#555}
  .drop-zone-hint{font-size:12px;color:#999;margin-top:4px}
  .drop-zone input[type=file]{position:absolute;inset:0;opacity:0;cursor:pointer}
  .file-list{margin-top:16px;display:none}
  .file-list.visible{display:block}
  .file-item{display:flex;align-items:center;justify-content:space-between;background:#fff;padding:8px 12px;border-radius:6px;margin-bottom:4px;font-size:13px;border:1px solid #e8e8e8}
  .file-item .name{font-family:monospace;color:#333}
  .file-item .size{color:#999;font-size:12px;margin-left:8px}
  .file-item .remove{cursor:pointer;color:#ff4d4f;background:none;border:none;font-size:18px;padding:0 4px}
  .btn-group{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap}
  .btn{padding:10px 24px;border-radius:8px;border:none;font-size:15px;font-weight:500;cursor:pointer;transition:all .15s;display:inline-flex;align-items:center;gap:6px}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .btn-primary{background:#4A90D9;color:#fff}
  .btn-primary:hover:not(:disabled){background:#357ABD}
  .btn-flash{background:#722ed1;color:#fff}
  .btn-flash:hover:not(:disabled){background:#531dab}
  .btn-outline{background:#fff;color:#555;border:1px solid #d9d9d9}
  .btn-outline:hover:not(:disabled){border-color:#4A90D9;color:#4A90D9}
  .status{margin-top:16px;padding:12px 16px;border-radius:8px;font-size:14px;display:none;line-height:1.6}
  .status.visible{display:block}
  .status.loading{background:#e6f7ff;border:1px solid #91d5ff;color:#0050b3}
  .status.success{background:#f6ffed;border:1px solid #b7eb8f;color:#135200}
  .status.error{background:#fff2f0;border:1px solid #ffccc7;color:#820014}
  .status.flash-progress{background:#f9f0ff;border:1px solid #d3adf7;color:#391085}
  @keyframes spin{to{transform:rotate(360deg)}}
  .spinner{display:inline-block;animation:spin .8s linear infinite}
  .progress-bar{margin-top:8px;width:100%;height:6px;background:#e8e8e8;border-radius:3px;overflow:hidden;display:none}
  .progress-bar.visible{display:block}
  .progress-bar .fill{height:100%;background:#4A90D9;border-radius:3px;transition:width .3s;width:0%}
  .log-box{margin-top:12px;background:#1e1e1e;color:#d4d4d4;border-radius:8px;padding:12px 16px;font-family:monospace;font-size:12px;max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-all;display:none}
  .log-box.visible{display:block}
  .info-bar{display:flex;gap:16px;flex-wrap:wrap;margin-top:24px;padding:16px;background:#fff;border-radius:8px;font-size:13px;color:#666}
  .info-bar .label{color:#999}
  .info-bar .value{font-weight:500;color:#333}
</style>
</head>
<body>
<div class="container" style="position:relative">
  <div class="lang-switch">
    <button id="langZh">中文</button>
    <button id="langEn">English</button>
  </div>
  <h1 id="mainTitle">K10 固件编译</h1>
  <p class="subtitle" id="pageSubtitle">上传项目 → 编译 → 浏览器烧录到行空板</p>

  <div class="drop-zone" id="dropZone">
    <div class="drop-zone-icon">📂</div>
    <div class="drop-zone-text" id="dropZoneText">点击选择文件，或拖拽文件到此处</div>
    <div class="drop-zone-hint" id="dropZoneHint">选择所有项目文件：至少需要 platformio.ini 和 src/main.cpp</div>
    <input type="file" id="fileInput" multiple>
  </div>

  <div class="file-list" id="fileList"></div>

  <div class="btn-group">
    <button class="btn btn-primary" id="btnCompile" disabled>▶ 编译</button>
    <button class="btn btn-outline" id="btnClear">🗑 清空</button>
  </div>

  <div class="status" id="status"></div>
  <div class="progress-bar" id="progressBar"><div class="fill" id="progressFill"></div></div>

  <div class="btn-group" id="flashGroup" style="display:none">
    <button class="btn btn-flash" id="btnFlash">⚡ 浏览器烧录</button>
    <button class="btn btn-flash" id="btnSrvFlash" style="background:#08979c">🖥️ 服务器烧录</button>
    <button class="btn btn-success" id="btnDownload">💾 下载 firmware.bin</button>
  </div>
  <div id="flashStatus" style="display:none;margin-top:8px;padding:10px 14px;border-radius:8px;font-size:13px"></div>

  <div class="log-box" id="logBox"></div>

  <div class="info-bar">
    <div><span class="label" id="svcLabel">服务器：</span><span class="value" id="svcStatus">检查中...</span></div>
    <div><span class="label" id="queueLabel">队列：</span><span class="value" id="svcQueue">-</span></div>
  </div>
</div>

<script type="module">
const I18N = {
  "zh-CN": {
    title: "K10 固件编译",
    subtitle: "上传项目 → 编译 → 浏览器烧录到行空板",
    chooseFiles: "点击选择文件，或拖拽文件到此处",
    chooseFilesHint: "选择所有项目文件：至少需要 platformio.ini 和 src/main.cpp",
    compile: "编译",
    clear: "清空",
    browserFlash: "浏览器烧录",
    serverFlash: "服务器烧录",
    download: "下载 firmware.bin",
    firmwareReady: "固件已就绪。请点击“浏览器烧录”写入本机 K10。",
    submitCompile: "提交编译请求...",
    compileSuccess: "编译成功！",
    compileFailed: "编译失败：",
    selectPort: "请选择 K10 串口",
    connectingK10: "正在连接 K10...",
    autoBoot: "正在尝试自动进入烧录模式...",
    manualBoot: "自动进入失败：请按住 BOOT 不放，点按 RST，继续按住 BOOT，等待连接...",
    loadingFlashFiles: "正在加载烧录文件...",
    flashing: "烧录中...",
    rebooting: "烧录完成，正在重启 K10...",
    flashDone: "烧录完成！K10 正在重启。",
    manualReset: "如果 K10 没有自动运行，请按一下 RST。",
    flashDoneFallback: "烧录完成！请按一下 K10 的 RST 键启动新程序。",
    webSerialUnsupported: "请使用 Chrome/Edge 通过 HTTPS 访问。",
    buildNotFound: "找不到 build_id：",
    buildNotDone: "该 build 尚未完成，当前状态：",
    manifestFailed: "无法加载烧录文件清单",
    loadBuildFailed: "加载 build 失败：",
    serverFlashHint: "将 K10 通过 USB-C 连接到本服务器，按住 BOOT+按 RST+松开 BOOT，然后等待...",
    serverFlashSuccess: "服务器烧录成功！K10 重启中...",
    serverFlashDone: "烧录成功！K10 已重启",
    serverFlashFailed: "服务器烧录失败",
    unknownError: "未知错误",
    requestFailed: "请求失败：",
    networkError: "网络错误：",
    cancelled: "已取消",
    flashFailed: "烧录失败：",
    missingPlatformio: "缺少 platformio.ini",
    queued: "排队中",
    ahead: "个）...",
    compilingInQueue: "编译中（排队: ",
    compiled: "编译成功！",
    ready: "就绪",
    notReady: "未就绪",
    cannotConnect: "无法连接",
    server: "服务器：",
    queue: "队列：",
    compilingCount: " 编译中 / ",
    waitingCount: " 等待",
    buildLoading: "正在加载已编译固件：",
    sizeUnit: "，大小 ",
    sizeUnitEn: " KB",
  },
  "en": {
    title: "K10 Firmware Compiler",
    subtitle: "Upload project → Compile → Flash to K10 in browser",
    chooseFiles: "Click to choose files, or drag files here",
    chooseFilesHint: "Select all project files: platformio.ini and src/main.cpp are required",
    compile: "Compile",
    clear: "Clear",
    browserFlash: "Browser Flash",
    serverFlash: "Server Flash",
    download: "Download firmware.bin",
    firmwareReady: "Firmware is ready. Click Browser Flash to write it to your local K10.",
    submitCompile: "Submitting compile job...",
    compileSuccess: "Compile complete!",
    compileFailed: "Compile failed: ",
    selectPort: "Select the K10 serial port",
    connectingK10: "Connecting to K10...",
    autoBoot: "Trying to enter flashing mode automatically...",
    manualBoot: "Auto boot failed: hold BOOT, tap RST, keep holding BOOT, wait for connection...",
    loadingFlashFiles: "Loading flash files...",
    flashing: "Flashing...",
    rebooting: "Flash complete. Rebooting K10...",
    flashDone: "Flash complete. K10 is rebooting.",
    manualReset: "If K10 does not start automatically, press RST once.",
    flashDoneFallback: "Flash complete. Press RST on K10 to start the new program.",
    webSerialUnsupported: "Please use Chrome/Edge over HTTPS.",
    buildNotFound: "Build ID not found: ",
    buildNotDone: "This build is not complete. Current status: ",
    manifestFailed: "Failed to load flash manifest",
    loadBuildFailed: "Failed to load build: ",
    serverFlashHint: "Connect K10 to this server over USB-C, hold BOOT + press RST + release BOOT, then wait...",
    serverFlashSuccess: "Server flash complete. K10 is rebooting...",
    serverFlashDone: "Flash success! K10 has restarted",
    serverFlashFailed: "Server flash failed",
    unknownError: "Unknown error",
    requestFailed: "Request failed: ",
    networkError: "Network error: ",
    cancelled: "Cancelled",
    flashFailed: "Flash failed: ",
    missingPlatformio: "Missing platformio.ini",
    queued: "Queued",
    ahead: " ahead)",
    compilingInQueue: "Compiling (queue: ",
    compiled: "Compiled successfully!",
    ready: "Ready",
    notReady: "Not ready",
    cannotConnect: "Cannot connect",
    server: "Server: ",
    queue: "Queue: ",
    compilingCount: " compiling / ",
    waitingCount: " waiting",
    buildLoading: "Loading build: ",
    sizeUnit: ", ",
    sizeUnitEn: " KB",
  }
};

const $ = id => document.getElementById(id);
const dz=$('dropZone'),fi=$('fileInput'),fl=$('fileList'),bc=$('btnCompile'),bcl=$('btnClear'),bf=$('btnFlash'),bsf=$('btnSrvFlash'),bd=$('btnDownload'),fg=$('flashGroup'),fs=$('flashStatus'),st=$('status'),lb=$('logBox'),pb=$('progressBar'),pf=$('progressFill');
let files=[],lastId=null,fwData=null,fwManifest=null;

function getInitialLang() {
  const params = new URLSearchParams(location.search);
  const fromUrl = params.get('lang');
  if (fromUrl && I18N[fromUrl]) return fromUrl;
  const saved = localStorage.getItem('k10-lang');
  if (saved && I18N[saved]) return saved;
  return (navigator.language && navigator.language.startsWith('zh')) ? 'zh-CN' : 'en';
}

let lang = getInitialLang();
let t = I18N[lang];

function setLang(nextLang) {
  lang = nextLang;
  t = I18N[lang];
  localStorage.setItem('k10-lang', lang);
  // Update active button style
  document.getElementById('langZh').className = lang === 'zh-CN' ? 'active' : '';
  document.getElementById('langEn').className = lang === 'en' ? 'active' : '';
  renderText();
}

function renderText() {
  document.querySelector('#mainTitle').textContent = t.title;
  document.querySelector('#pageTitle').textContent = t.title;
  document.querySelector('#pageSubtitle').textContent = t.subtitle;
  document.querySelector('#dropZoneText').textContent = t.chooseFiles;
  document.querySelector('#dropZoneHint').textContent = t.chooseFilesHint;
  document.querySelector('#btnCompile').innerHTML = '▶ ' + t.compile;
  document.querySelector('#btnClear').innerHTML = '🗑 ' + t.clear;
  document.querySelector('#btnFlash').innerHTML = '⚡ ' + t.browserFlash;
  document.querySelector('#btnSrvFlash').innerHTML = '🖥️ ' + t.serverFlash;
  document.querySelector('#btnDownload').innerHTML = '💾 ' + t.download;
  document.querySelector('#svcLabel').textContent = t.server;
  document.querySelector('#queueLabel').textContent = t.queue;
  document.title = t.title;
}

document.getElementById('langZh').addEventListener('click', () => setLang('zh-CN'));
document.getElementById('langEn').addEventListener('click', () => setLang('en'));

async function loadBuildFromUrl() {
  const params = new URLSearchParams(location.search);
  const id = params.get('build_id') || params.get('build');
  const pathMatch = location.pathname.match(/^\/flash\/([^/]+)$/);
  const buildId = id || (pathMatch && pathMatch[1]);
  if (!buildId) return;

  lastId = buildId;
  show('loading', t.buildLoading + buildId);

  const statusResp = await fetch('/api/build/' + encodeURIComponent(buildId) + '/status');
  if (!statusResp.ok) {
    show('error', t.buildNotFound + buildId);
    return;
  }

  const status = await statusResp.json();
  if (status.status !== 'done') {
    show('error', t.buildNotDone + status.status);
    return;
  }

  const manifestResp = await fetch('/api/build/' + encodeURIComponent(buildId) + '/flash-files');
  if (!manifestResp.ok) {
    show('error', t.manifestFailed);
    return;
  }

  fwManifest = await manifestResp.json();
  fg.style.display = 'flex';

  const sizeText = status.bin_size ? t.sizeUnit + Math.round(status.bin_size / 1024) + t.sizeUnitEn : '';
  show('success', t.firmwareReady + sizeText);
}

loadBuildFromUrl().catch(e => {
  show('error', t.loadBuildFailed + e.message);
});

dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('dragover')});
dz.addEventListener('dragleave',()=>dz.classList.remove('dragover'));
dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('dragover');addFiles([...e.dataTransfer.files])});
fi.addEventListener('change',()=>{addFiles([...fi.files]);fi.value=''});

function addFiles(nf){for(const f of nf){const p=f.webkitRelativePath||f.name;files=files.filter(f2=>(f2.webkitRelativePath||f2.name)!==p);files.push(f)}render()}

function render(){
  if(!files.length){fl.classList.remove('visible');bc.disabled=true;fg.style.display='none';return}
  fl.classList.add('visible');fg.style.display='none';
  const ini=files.some(f=>(f.webkitRelativePath||f.name).endsWith('platformio.ini'));
  fl.innerHTML=files.map((f,i)=>'<div class="file-item"><span><span class="name">'+(f.webkitRelativePath||f.name)+'</span><span class="size">'+(f.size/1024).toFixed(1)+' KB</span></span><button class="remove" data-i="'+i+'">×</button></div>').join('');
  fl.querySelectorAll('.remove').forEach(b=>b.addEventListener('click',()=>{files.splice(+b.dataset.i,1);render()}));
  bc.disabled=!ini;
  if(!ini)show('error','⚠️ ' + t.missingPlatformio);else hide()
}

bcl.addEventListener('click',()=>{files=[];lastId=null;fwData=null;fwManifest=null;render();hide();lb.classList.remove('visible');fg.style.display='none';history.replaceState(null,'','/')});

bc.addEventListener('click',async()=>{
  bc.disabled=true;fg.style.display='none';fwData=null;
  show('loading','⏳ ' + t.submitCompile);lb.classList.remove('visible');pb.classList.remove('visible');
  const fd=new FormData();
  for(const f of files)fd.append('files',f,f.webkitRelativePath||f.name);
  try{
    const r=await fetch('/api/compile/files',{method:'POST',body:fd}),d=await r.json();
    if(!r.ok){show('error','❌ '+d.error);bc.disabled=false;return}
    lastId=d.build_id;show('loading','⏳ ' + t.compilingInQueue + d.queue_position + ')...');pb.classList.add('visible');setPct(5);
    let done=false,c=0;
    while(!done){
      await new Promise(r=>setTimeout(r,2000));c++;
      const pr=await fetch('/api/build/'+lastId+'/status'),pd=await pr.json();
      setPct(pd.status==='compiling'?Math.min(10+c*3,90):pd.progress||0);
      if(pd.status==='done'){done=true;setPct(100);pb.classList.remove('visible');show('success','✅ ' + t.compiled + ' ' + (pd.bin_size/1024).toFixed(0) + ' KB / ' + pd.elapsed + 's');
        fetch('/api/build/'+lastId+'/flash-files').then(r=>r.ok&&r.json()).then(m=>{fwManifest=m;fg.style.display='flex'}).catch(()=>{})
      }else if(pd.status==='error'){done=true;pb.classList.remove('visible');show('error','❌ ' + t.compileFailed + (pd.error || ''));if(pd.log){lb.textContent=pd.log;lb.classList.add('visible')}}
      else if(pd.status==='queued')show('loading','⏳ ' + t.queued + '（前方 ' + (pd.queue_position||0) + t.ahead)
    }
  }catch(e){show('error','❌ ' + t.networkError + e.message);lb.textContent=e.stack||e.message;lb.classList.add('visible')}
  finally{bc.disabled=false}
});

bd.addEventListener('click',()=>{
  if(!fwData)return;const u=URL.createObjectURL(fwData),a=document.createElement('a');a.href=u;a.download='firmware.bin';a.click();URL.revokeObjectURL(u)
});

bf.addEventListener('click',async()=>{
  if(!fwData&&!fwManifest)return;
  if(!navigator.serial){show('error',t.webSerialUnsupported);return}
  bf.disabled=true;
  try{
    const port=await navigator.serial.requestPort();
    show('loading',t.connectingK10);
    const{ESPLoader}=await import('/static/esptool-js.mjs');
    const el=new ESPLoader({port,baudrate:115200});
    const origReadFlash=el.readFlashId;
    el.readFlashId=async function(){try{return await origReadFlash.call(this)}catch(e){console.warn('跳过 readFlashId:',e);return 0}};
    show('loading','⏳ ' + t.autoBoot);
    try {
      await el.main('default_reset');
    } catch (autoResetError) {
      console.warn('自动进入烧录模式失败，改用手动 BOOT/RST:', autoResetError);
      show('loading','⏳ ' + t.manualBoot);
      await new Promise(r=>setTimeout(r,4000));
      await el.main('no_reset');
    }

    // 下载 flash-files 清单中的所有文件
    const manifest=fwManifest||await(await fetch('/api/build/'+lastId+'/flash-files')).json();
    const fileArray=[];
    for(const f of manifest.files){
      const resp=await fetch('/api/build/'+lastId+'/file/'+f.filename);
      if(resp.ok){
        const data=new Uint8Array(await resp.arrayBuffer());
        fileArray.push({data,address:parseInt(f.offset)});

      }
    }

    show('loading','⏳ ' + t.flashing);
    await el.writeFlash({
      fileArray,
      flashMode:'keep',flashFreq:'keep',flashSize:'keep',
      compress:true,eraseAll:false,
      reportProgress:(i,cur,total)=>{if(total>0)setPct(Math.round(cur/total*100))}
    });

    // DTR/RTS 信号序列：让 ESP32-S3 退出下载模式并运行新固件
    function delay(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function resetToFirmware(port) {
      await port.setSignals({ dataTerminalReady: false, requestToSend: true });
      await delay(100);
      await port.setSignals({ dataTerminalReady: false, requestToSend: false });
      await delay(250);
    }

    try {
      show('loading', t.rebooting);
      await resetToFirmware(port);
      await delay(800);
      setPct(100);
      show('success', '✅ ' + t.flashDone + ' ' + t.manualReset);
    } catch (e) {
      console.warn('自动重启失败，可能需要手动按 RST:', e);
      setPct(100);
      show('success', '✅ ' + t.flashDoneFallback);
    } finally {
      try {
        if (el.transport && typeof el.transport.disconnect === 'function') {
          await el.transport.disconnect();
        } else if (port && typeof port.close === 'function') {
          await port.close();
        }
      } catch (closeError) {
        console.warn('关闭串口失败，可忽略:', closeError);
      }
    }
  }catch(e){if(e.name==='NotFoundError')show('error',t.cancelled);else show('error',t.flashFailed + e.message)}
  finally{bf.disabled=false;setPct(0)}
});

bsf.addEventListener('click',async()=>{
  if(!lastId)return;
  bsf.disabled=true;show('loading',t.serverFlashHint);
  fs.style.display='block';fs.style.background='#e6f7ff';fs.style.border='1px solid #91d5ff';fs.style.color='#0050b3';
  fs.innerHTML=t.serverFlashHint;
  try{
    const r=await fetch('/api/flash/'+lastId,{method:'POST'});
    const d=await r.json();
    if(d.status==='success'){show('success',t.serverFlashSuccess);
      fs.style.background='#f6ffed';fs.style.border='1px solid #b7eb8f';fs.style.color='#135200';
      fs.innerHTML=t.serverFlashDone}
    else{show('error',t.serverFlashFailed);fs.style.background='#fff2f0';fs.style.border='1px solid #ffccc7';fs.style.color='#820014';fs.innerHTML='<pre style="white-space:pre-wrap;font-size:12px">'+(d.log||d.error||t.unknownError)+'</pre>'}
  }catch(e){show('error',t.requestFailed + e.message)}
  finally{bsf.disabled=false}
});

function show(t,m){st.className='status '+t+' visible';st.innerHTML=m}
function hide(){st.className='status'}
function setPct(p){pf.style.width=p+'%';pb.classList.add('visible')}

async function fetchStatus(){
  try{const r=await fetch('/api/health'),d=await r.json();$('svcStatus').textContent=d.k10_toolchain_ready?'✅ ' + t.ready:'⚠️ ' + t.notReady;$('svcQueue').textContent=d.active_compiles + t.compilingCount + d.waiting_in_queue + t.waitingCount}
  catch{$('svcStatus').textContent='❌ ' + t.cannotConnect}
}
fetchStatus();setInterval(fetchStatus,5000);

// Initialize language
setLang(lang);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/flash/{build_id}", response_class=HTMLResponse)
async def web_index():
    return WEB_PAGE


@app.get("/api/health")
async def health():
    try:
        r = subprocess.run(["pio", "--version"], capture_output=True, text=True, timeout=10)
        pio_ver = r.stdout.strip()
    except Exception as e:
        pio_ver = f"unavailable: {e}"

    k10_ready = any(
        "unihiker" in d.name.lower()
        for d in (Path.home() / ".platformio" / "platforms").iterdir()
        if (Path.home() / ".platformio" / "platforms").is_dir() and d.is_dir()
    )

    return {
        "status": "ok",
        "version": VERSION,
        "pio_version": pio_ver,
        "k10_toolchain_ready": k10_ready,
        "max_concurrent_compiles": MAX_CONCURRENT_COMPILES,
        "active_compiles": MAX_CONCURRENT_COMPILES - _compile_semaphore._value,
        "waiting_in_queue": _waiting_count,
        "active_builds": len(_build_results),
        "uptime_seconds": int(time.time() - _start_time),
    }


@app.post("/api/compile")
async def compile_zip(request: Request, file: UploadFile = File(...)):
    client_ip = request.client.host
    logger.info(f"[zip] from {client_ip}, file: {file.filename}")

    if not file.filename or not file.filename.endswith(".zip"):
        return JSONResponse(status_code=400, content={"error": "仅支持 .zip 文件"})

    content = await file.read()
    if len(content) == 0:
        return JSONResponse(status_code=400, content={"error": "上传文件为空"})
    if len(content) > UPLOAD_SIZE_LIMIT:
        return JSONResponse(status_code=400, content={"error": f"文件大小超过 {UPLOAD_SIZE_LIMIT // 1024 // 1024}MB 限制"})

    tmp_dir = tempfile.mkdtemp(prefix="k10_compile_")
    try:
        with open(os.path.join(tmp_dir, "upload.zip"), "wb") as f:
            f.write(content)
        with zipfile.ZipFile(os.path.join(tmp_dir, "upload.zip"), "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # Per-entry path traversal check: resolve and confirm within tmp_dir
                resolved = os.path.realpath(os.path.join(tmp_dir, info.filename))
                if not resolved.startswith(os.path.realpath(tmp_dir) + os.sep):
                    return JSONResponse(status_code=400, content={"error": "压缩包包含不安全的路径"})
                os.makedirs(os.path.dirname(resolved), exist_ok=True)
                with open(resolved, "wb") as out:
                    out.write(zf.read(info.filename))

        project_dir = _find_project_dir(tmp_dir)
        if not project_dir:
            return JSONResponse(status_code=400, content={"error": "未找到 platformio.ini"})

        return await _submit_compile(project_dir, client_ip, tmp_dir)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/compile/files")
async def compile_files(request: Request, files: list[UploadFile] = File(...)):
    client_ip = request.client.host
    logger.info(f"[files] from {client_ip}, {len(files)} files")

    if not files:
        return JSONResponse(status_code=400, content={"error": "未上传文件"})

    tmp_dir = tempfile.mkdtemp(prefix="k10_compile_")
    try:
        # 1. 将所有上传文件写入临时目录（保留原始目录结构）
        uploaded_paths = []
        for f in files:
            if not f.filename:
                continue
            safe = f.filename.replace("\\", "/").lstrip("/")
            if ".." in safe:
                continue
            dest = os.path.join(tmp_dir, safe)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            data = await f.read()
            with open(dest, "wb") as wf:
                wf.write(data)
            uploaded_paths.append(dest)

        logger.info(f"[files] from {client_ip}: {len(files)} files uploaded to {tmp_dir}")

        # 2. 查找 platformio.ini
        ini_files = sorted(Path(tmp_dir).rglob("platformio.ini"))
        if not ini_files:
            return JSONResponse(status_code=400, content={"error": "未找到 platformio.ini"})

        ini_path = ini_files[0]
        project_dir = str(ini_path.parent)
        logger.info(f"[files] found platformio.ini at {project_dir}")

        # 3. 确保项目根目录有 src/ 并且包含源文件
        src_dir = os.path.join(project_dir, "src")
        if not os.path.isdir(src_dir) or not list(os.listdir(src_dir)):
            # 源文件可能直接放在项目根目录，需要挪到 src/
            all_sources = list(Path(project_dir).rglob("*.cpp")) + list(Path(project_dir).rglob("*.c")) + list(Path(project_dir).rglob("*.ino"))
            # 排除已存在的 src/ 下的文件（如果有）
            if os.path.isdir(src_dir):
                all_sources = [s for s in all_sources if not str(s).startswith(str(Path(src_dir)))]
            if all_sources:
                os.makedirs(src_dir, exist_ok=True)
                for sf in all_sources:
                    dest = os.path.join(src_dir, sf.name)
                    if not os.path.isfile(dest):
                        shutil.copy2(str(sf), dest)
                        logger.info(f"[files] copied {sf.name} → src/")

        # 4. 再次检查 src/ 是否有文件
        if not os.path.isdir(src_dir) or not list(os.listdir(src_dir)):
            return JSONResponse(status_code=400, content={
                "error": "未找到源代码文件",
                "hint": "请确保项目文件夹内包含 .cpp 或 .ino 源文件",
            })

        # 5. 检查 partitions.csv：如果 platformio.ini 引用了分区表但文件缺失，报错
        ini_content = Path(ini_path).read_text()
        needs_partitions = "partitions" in ini_content.lower()
        has_partitions = list(Path(project_dir).glob("partitions.csv"))
        if needs_partitions and not has_partitions:
            return JSONResponse(status_code=400, content={
                "error": "缺少 partitions.csv",
                "hint": "platformio.ini 引用了分区表，请提供 partitions.csv 文件",
            })

        return await _submit_compile(project_dir, client_ip, tmp_dir)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


async def _submit_compile(project_dir: str, client_ip: str, tmp_dir: str):
    global _waiting_count
    build_id = uuid.uuid4().hex[:8]
    result = new_build_result()
    result["tmp_dir"] = tmp_dir
    _build_results[build_id] = result

    _waiting_count += 1
    queue_pos = _waiting_count
    logger.info(f"build_id={build_id}: 已提交（队列位置 {queue_pos}），来自 {client_ip}")

    def _run():
        global _waiting_count
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _task():
            global _waiting_count
            async with _compile_semaphore:
                _waiting_count -= 1
                result["status"] = "compiling"
                result["progress"] = 10
                logger.info(f"build_id={build_id}: 开始编译...")
                compile_start = time.time()

                try:
                    proc = await asyncio.create_subprocess_exec(
                        "pio", "run",
                        cwd=project_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(
                            proc.communicate(), timeout=COMPILE_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        proc.kill()
                        result["status"] = "error"
                        result["error"] = "编译超时（超过 5 分钟）"
                        result["finished_at"] = time.time()
                        return

                    elapsed = time.time() - compile_start
                    log_text = (stdout or b"").decode("utf-8", errors="replace") + "\n" + (stderr or b"").decode("utf-8", errors="replace")
                    result["log"] = log_text[-MAX_LOG_LENGTH:] if len(log_text) > MAX_LOG_LENGTH else log_text

                    if proc.returncode != 0:
                        result["status"] = "error"
                        result["error"] = "编译失败"
                        result["finished_at"] = time.time()
                        logger.info(f"build_id={build_id}: 编译失败 ({elapsed:.1f}s)")
                        return

                    build_dir = _find_build_dir(project_dir)
                    if not build_dir:
                        result["status"] = "error"
                        result["error"] = "编译成功但未找到构建输出目录"
                        result["finished_at"] = time.time()
                        return

                    flash_files = _collect_flash_files(build_dir)
                    if not flash_files or "firmware" not in flash_files:
                        result["status"] = "error"
                        result["error"] = "编译成功但未找到固件文件"
                        result["finished_at"] = time.time()
                        return

                    result["status"] = "done"
                    result["build_dir"] = build_dir
                    result["flash_files"] = flash_files
                    result["bin_path"] = flash_files["firmware"]["path"]
                    result["bin_size"] = flash_files["firmware"]["size"]
                    result["progress"] = 100
                    result["finished_at"] = time.time()
                    logger.info(f"build_id={build_id}: 完成 ({result['bin_size']} bytes, {elapsed:.1f}s)")

                except Exception as e:
                    result["status"] = "error"
                    result["error"] = str(e)
                    result["finished_at"] = time.time()
                    logger.exception(f"build_id={build_id}: 编译异常")

        loop.run_until_complete(_task())
        loop.close()

        def _cleanup():
            time.sleep(BUILD_RESULT_TTL)
            td = result.get("tmp_dir")
            if td and os.path.isdir(td):
                shutil.rmtree(td, ignore_errors=True)

        threading.Thread(target=_cleanup, daemon=True).start()

    threading.Thread(target=_run, daemon=True).start()

    return JSONResponse(content={
        "build_id": build_id,
        "queue_position": queue_pos,
        "status": "queued",
    })


@app.get("/api/build/{build_id}/status")
async def build_status(build_id: str):
    result = _build_results.get(build_id)
    if not result:
        return JSONResponse(status_code=404, content={"error": "build_id 不存在或已过期"})

    resp = {
        "status": result["status"],
        "progress": result["progress"],
        "elapsed": round(time.time() - result["created_at"], 1) if not result["finished_at"] else round(result["finished_at"] - result["created_at"], 1),
        "queue_position": _waiting_count if result["status"] == "queued" else 0,
    }

    if result["status"] == "error":
        resp["error"] = result["error"]
        resp["log"] = result.get("log", "")

    if result["status"] == "done":
        resp["bin_size"] = result["bin_size"]

    return JSONResponse(content=resp)


@app.get("/api/build/{build_id}/download")
async def build_download(build_id: str):
    result = _build_results.get(build_id)
    if not result:
        return JSONResponse(status_code=404, content={"error": "build_id 不存在或已过期"})
    if result["status"] != "done":
        return JSONResponse(status_code=400, content={"error": "编译尚未完成"})
    if not result["bin_path"] or not os.path.isfile(result["bin_path"]):
        return JSONResponse(status_code=500, content={"error": "固件文件不存在"})

    return FileResponse(
        path=result["bin_path"],
        media_type="application/octet-stream",
        filename="firmware.bin",
        headers={"X-Build-Id": build_id, "X-Build-Size": str(result["bin_size"])},
    )


@app.get("/api/build/{build_id}/flash-files")
async def build_flash_files(build_id: str):
    result = _build_results.get(build_id)
    if not result:
        return JSONResponse(status_code=404, content={"error": "build_id 不存在或已过期"})
    if result["status"] != "done":
        return JSONResponse(status_code=400, content={"error": "编译尚未完成"})

    flash_files = result.get("flash_files", {})
    if not flash_files:
        return JSONResponse(status_code=500, content={"error": "无烧录文件信息"})

    return JSONResponse(content={
        "build_id": build_id,
        "files": [
            {
                "name": key,
                "filename": f"{key}.bin",
                "offset": info["offset"],
                "size": info["size"],
            }
            for key, info in flash_files.items()
        ],
    })


@app.get("/api/build/{build_id}/file/{filename}")
async def build_file_download(build_id: str, filename: str):
    result = _build_results.get(build_id)
    if not result:
        return JSONResponse(status_code=404, content={"error": "build_id 不存在或已过期"})
    if result["status"] != "done":
        return JSONResponse(status_code=400, content={"error": "编译尚未完成"})

    flash_files = result.get("flash_files", {})
    for info in flash_files.values():
        if Path(info["path"]).name == filename:
            if os.path.isfile(info["path"]):
                return FileResponse(
                    path=info["path"],
                    media_type="application/octet-stream",
                    filename=filename,
                )
            return JSONResponse(status_code=404, content={"error": "文件不存在"})

    return JSONResponse(status_code=404, content={"error": f"未找到文件 {filename}"})


# ── 服务端烧录（K10 插到服务器上）──
@app.post("/api/flash/{build_id}")
async def flash_device(build_id: str, request: Request):
    """服务器端烧录（K10 插到服务器上）。

    可选 POST body 参数:
      - port: 串口设备路径（如 /dev/ttyUSB0），默认自动检测
    """
    result = _build_results.get(build_id)
    if not result or result["status"] != "done" or not result.get("bin_path"):
        return JSONResponse(status_code=400, content={"error": "编译结果不存在或未完成"})

    # 读取可选的 port 参数
    port = FLASH_PORT
    try:
        body = await request.json()
        port = body.get("port", port)
    except Exception:
        pass

    flash_files = result.get("flash_files", {})
    if not flash_files:
        return JSONResponse(status_code=500, content={"error": "无烧录文件信息"})

    logger.info(f"flash {build_id}: 开始烧录（{len(flash_files)} 个文件）")
    try:
        flash_args = ["python3", "-m", "esptool", "--chip", "esp32s3"]
        if port:
            flash_args.extend(["--port", port])
        flash_args.append("write_flash")
        if len(flash_files) > 1:
            flash_args.append("--compress")
        for info in flash_files.values():
            if os.path.isfile(info["path"]):
                flash_args.extend([info["offset"], info["path"]])

        if len(flash_args) <= 6:
            bin_path = result.get("bin_path")
            if bin_path and os.path.isfile(bin_path):
                flash_args = ["python3", "-m", "esptool", "--chip", "esp32s3"]
                if port:
                    flash_args.extend(["--port", port])
                flash_args.extend(["write_flash", "0x0", bin_path])
            else:
                return JSONResponse(status_code=500, content={"error": "固件文件不存在"})

        proc = subprocess.run(flash_args, capture_output=True, text=True, timeout=120)
        output = proc.stdout + proc.stderr
        if proc.returncode == 0:
            logger.info(f"flash {build_id}: 烧录成功")
            return JSONResponse(content={"status": "success", "log": output[-2000:]})
        else:
            logger.error(f"flash {build_id}: 失败")
            return JSONResponse(status_code=500, content={"status": "error", "log": output[-2000:]})
    except subprocess.TimeoutExpired:
        return JSONResponse(status_code=408, content={"error": "烧录超时"})
    except FileNotFoundError:
        return JSONResponse(status_code=500, content={"error": "未找到 esptool.py"})


def _find_project_dir(base_dir: str) -> Optional[str]:
    if os.path.isfile(os.path.join(base_dir, "platformio.ini")):
        return base_dir
    try:
        for item in os.listdir(base_dir):
            sub = os.path.join(base_dir, item)
            if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "platformio.ini")):
                return sub
    except PermissionError:
        pass
    return None


def _find_build_dir(project_dir: str) -> Optional[str]:
    """Find the PlatformIO build output directory."""
    pio_dir = Path(project_dir) / ".pio" / "build"
    if pio_dir.is_dir():
        for env_dir in pio_dir.iterdir():
            if env_dir.is_dir():
                return str(env_dir)
    return None


def _collect_flash_files(build_dir: str) -> dict:
    """Collect all flashable bin files with their ESP32-S3 offsets.

    Standard ESP32-S3 flash layout:
      0x0       - bootloader.bin
      0x8000    - partitions.bin
      0x10000   - firmware.bin (application)
    """
    files = {}
    bd = Path(build_dir)

    mapping = {
        "bootloader": ("bootloader.bin", "0x0"),
        "partitions": ("partitions.bin", "0x8000"),
        "firmware": ("firmware.bin", "0x10000"),
    }

    for key, (filename, offset) in mapping.items():
        path = bd / filename
        if path.is_file():
            files[key] = {
                "path": str(path),
                "offset": offset,
                "size": path.stat().st_size,
            }

    return files


def _cleanup_loop():
    while True:
        time.sleep(CLEANUP_INTERVAL)
        now = time.time()
        expired = [bid for bid, r in _build_results.items()
                   if r["finished_at"] and (now - r["finished_at"]) > BUILD_RESULT_TTL]
        for bid in expired:
            r = _build_results.pop(bid, {})
            td = r.get("tmp_dir")
            if td and os.path.isdir(td):
                shutil.rmtree(td, ignore_errors=True)
            logger.info(f"build_id={bid}: 结果已过期清理")


threading.Thread(target=_cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    import uvicorn

    cert_path = os.environ.get("K10_SSL_CERT", os.path.join(os.path.dirname(__file__), "cert.pem"))
    key_path = os.environ.get("K10_SSL_KEY", os.path.join(os.path.dirname(__file__), "key.pem"))
    use_ssl = os.path.isfile(cert_path) and os.path.isfile(key_path)

    if use_ssl:
        # 启动 HTTP → HTTPS 重定向服务（后台线程）
        from http.server import HTTPServer, BaseHTTPRequestHandler
        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"https://{self.headers.get('Host', HOST)}:{PORT}{self.path}")
                self.end_headers()
            def log_message(self, *a): pass

        def start_redirect():
            httpd = HTTPServer(("0.0.0.0", REDIRECT_PORT), RedirectHandler)
            httpd.serve_forever()

        import threading
        threading.Thread(target=start_redirect, daemon=True).start()
        print(f"HTTP  redirect: http://{HOST}:{REDIRECT_PORT} → https://{HOST}:{PORT}")

        print(f"HTTPS server on https://{HOST}:{PORT}")
        uvicorn.run(app, host=HOST, port=PORT, log_level="info",
                    ssl_certfile=cert_path, ssl_keyfile=key_path)
    else:
        print(f"HTTP server on http://{HOST}:{PORT} (no SSL)")
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
