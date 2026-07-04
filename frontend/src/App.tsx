import React, {
  useState, useEffect, useRef, useMemo, useCallback
} from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ResponsiveContainer, AreaChart, Area,
  BarChart, Bar, Cell,
  XAxis, YAxis, Tooltip as ReTooltip
} from 'recharts'
import * as d3 from 'd3-force'
import {
  ShieldAlert, MessageSquare, UploadCloud, Layers,
  ChevronRight, ChevronLeft, Download, AlertTriangle,
  CheckCircle2, Send, Users, TrendingUp, ArrowRightLeft,
  Sparkles, Search as SearchIcon, Maximize2, Minimize2,
  Network, FileText, Activity, Eye, BookOpen,
  Crosshair, Radio, Zap, Lock, Globe, X
} from 'lucide-react'
import './App.css'

const API_BASE = 'http://localhost:8000/api/v1'

/* ─────────── Types ─────────── */
interface Stats {
  total_accounts: number; suspect_accounts: number
  total_transactions: number; flagged_transactions: number
  fraud_rings_detected: number; open_investigations: number
  total_amount_at_risk: number; banks_covered: string[]
}
interface Account {
  account_id: string; holder_name: string; bank_name: string
  risk_score: number; is_suspect: boolean; fraud_role?: string
  fraud_ring_id?: string; active_patterns?: string
  risk_reasoning?: string; isolation_mean_score?: number
  isolation_max_score?: number
}
interface Tx {
  id: string; date: string; time: string; narration: string
  channel: string; debit: number; credit: number; balance: number
  utr_ref: string; counterparty_account_id?: string
  counterparty_name?: string; is_high_value_flag: boolean
  is_balance_breach: boolean
}
interface Ring {
  ring_id: string; typology: string; status: string
  confidence_score: number; total_accounts: number
  total_amount_moved: number
}
interface ChatMsg {
  sender: 'user' | 'ai'; text: string
  sources?: string[]; ts: number
}
interface ForceNode extends d3.SimulationNodeDatum {
  id: string; label: string; bank: string; risk: number
  type: 'target' | 'sender' | 'receiver'
  fx?: number | null; fy?: number | null
}
interface ForceLink extends d3.SimulationLinkDatum<ForceNode> {
  source: string | ForceNode; target: string | ForceNode; amount: number
}

/* ─────────── Mock data ─────────── */
const MOCK_STATS: Stats = {
  total_accounts: 19, suspect_accounts: 6, total_transactions: 14484,
  flagged_transactions: 5749, fraud_rings_detected: 13,
  open_investigations: 1, total_amount_at_risk: 492837.50,
  banks_covered: ['Bandhan Bank','IDFC FIRST Bank','IDBI Bank','Yes Bank']
}
const MOCK_ACCOUNTS: Account[] = [
  { account_id:'99572217148131', holder_name:'Indian', bank_name:'Bandhan Bank', risk_score:82.6, is_suspect:true, fraud_role:'Collector', fraud_ring_id:'1', active_patterns:'ROUND_TRIP|FAN_IN|FAN_OUT|SMURFING|HIGH_VALUE|BALANCE_BREACH', risk_reasoning:'Round-trip indicators present (0.77 intensity) | Collector behaviour: 14 senders | Distributor behaviour: 5 receivers | Structured transfers relative to own activity | Per-account high-value outliers | Statement balance continuity issues | New high-value beneficiary', isolation_mean_score:0.5597, isolation_max_score:0.7673 },
  { account_id:'18306700003', holder_name:'Unknown', bank_name:'IDFC FIRST Bank', risk_score:79.2, is_suspect:true, fraud_role:'Collector', fraud_ring_id:'10', active_patterns:'ROUND_TRIP|FAN_IN|FAN_OUT|SMURFING|VELOCITY|HIGH_VALUE|BALANCE_BREACH', risk_reasoning:'Round-trip indicators present (0.39 intensity) | Collector behaviour: 33 senders | Distributor behaviour: 5 receivers | Velocity bursts | Per-account high-value outliers | Statement balance issues | New high-value beneficiary', isolation_mean_score:0.8530, isolation_max_score:1.0000 },
  { account_id:'8642666611469255', holder_name:'Unknown', bank_name:'IDBI Bank', risk_score:67.7, is_suspect:true, fraud_role:'Collector', fraud_ring_id:'8', active_patterns:'ROUND_TRIP|FAN_IN|FAN_OUT|SMURFING|HIGH_VALUE|BALANCE_BREACH', risk_reasoning:'Round-trip indicators present (0.61 intensity) | Collector behaviour: 13 senders | Distributor behaviour: 10 receivers | Structured transfers | Per-account high-value outliers | Statement balance continuity issues', isolation_mean_score:0.7070, isolation_max_score:0.8699 },
  { account_id:'00869354051', holder_name:'Unknown', bank_name:'IDFC FIRST Bank', risk_score:57.4, is_suspect:true, fraud_role:'Distributor', fraud_ring_id:'11', active_patterns:'FAN_IN|FAN_OUT|SMURFING|VELOCITY|HIGH_VALUE|BALANCE_BREACH', risk_reasoning:'Collector behaviour: 24 senders | Distributor behaviour: 33 receivers | Structured transfers relative to own activity | Velocity bursts | Per-account high-value outliers | Statement balance continuity issues | New high-value beneficiary', isolation_mean_score:0.7706, isolation_max_score:0.8901 },
  { account_id:'17771917925', holder_name:'Unknown', bank_name:'IDFC FIRST Bank', risk_score:46.2, is_suspect:false, fraud_role:'Distributor', fraud_ring_id:'13', active_patterns:'FAN_IN|FAN_OUT|SMURFING|VELOCITY|HIGH_VALUE', risk_reasoning:'Collector behaviour: 15 senders | Distributor behaviour: 25 receivers | Structured transfers relative to own activity | Velocity bursts | Per-account high-value outliers | New high-value beneficiary', isolation_mean_score:0.8109, isolation_max_score:0.9131 },
  { account_id:'098030016134598', holder_name:'Unknown', bank_name:'Yes Bank', risk_score:35.0, is_suspect:false, fraud_role:'Distributor', fraud_ring_id:'7', active_patterns:'FAN_IN|FAN_OUT|SMURFING|HIGH_VALUE|NEW_HV_BENE|GRAPH_CENTRAL', risk_reasoning:'Collector behaviour: 101 senders | Distributor behaviour: 3260 receivers | Structured transfers relative to own activity | Per-account high-value outliers | New high-value beneficiary | Graph-central account by PageRank/betweenness/degree', isolation_mean_score:0.1268, isolation_max_score:0.8740 }
]
const MOCK_RINGS: Ring[] = [
  { ring_id:'1', typology:'louvain_community', status:'detected', confidence_score:0.85, total_accounts:4, total_amount_moved:125400.0 },
  { ring_id:'7', typology:'louvain_community', status:'detected', confidence_score:0.90, total_accounts:7, total_amount_moved:320000.5 },
  { ring_id:'10', typology:'louvain_community', status:'detected', confidence_score:0.80, total_accounts:3, total_amount_moved:94000.0 },
  { ring_id:'8', typology:'louvain_community', status:'detected', confidence_score:0.75, total_accounts:5, total_amount_moved:180000.0 },
  { ring_id:'11', typology:'louvain_community', status:'detected', confidence_score:0.88, total_accounts:6, total_amount_moved:215000.0 },
]
const MOCK_TXS: Tx[] = [
  { id:'1', date:'2026-06-25', time:'10:14:22', narration:'TRF FROM AGENT MULE / NEFT', channel:'NEFT', debit:0, credit:145000, balance:145200, utr_ref:'NEFTUTRN2617632', is_high_value_flag:false, is_balance_breach:false },
  { id:'2', date:'2026-06-25', time:'10:18:05', narration:'TRF TO PRIMARY LAYERER / IMPS', channel:'IMPS', debit:144800, credit:0, balance:400, utr_ref:'IMPSUTR2677810', is_high_value_flag:true, is_balance_breach:false },
  { id:'3', date:'2026-06-26', time:'14:22:11', narration:'ATM CASH WITHDRAWAL OUTLIER', channel:'ATM', debit:40000, credit:0, balance:-39600, utr_ref:'ATM1872190', is_high_value_flag:true, is_balance_breach:true },
  { id:'4', date:'2026-06-27', time:'09:05:00', narration:'IMPS RECEIVED FROM SHELL', channel:'IMPS', debit:0, credit:180000, balance:140400, utr_ref:'IMPS998782', is_high_value_flag:false, is_balance_breach:false },
  { id:'5', date:'2026-06-27', time:'09:08:12', narration:'RTGS DISTRIBUTED OUTFLOW', channel:'RTGS', debit:178000, credit:0, balance:2400, utr_ref:'RTGS887211', is_high_value_flag:true, is_balance_breach:false },
  { id:'6', date:'2026-06-28', time:'11:30:00', narration:'UPI/512918/FROM COLLECTOR NODE', channel:'UPI', debit:0, credit:55000, balance:57400, utr_ref:'UPIRTS992', is_high_value_flag:false, is_balance_breach:false },
  { id:'7', date:'2026-06-28', time:'11:45:10', narration:'NEFT OUTWARD TO LAYERER B', channel:'NEFT', debit:54500, credit:0, balance:2900, utr_ref:'NEFT883921', is_high_value_flag:true, is_balance_breach:false },
]
const MOCK_CPS: Account[] = [
  { account_id:'18306700003', holder_name:'Aggregator A', bank_name:'IDFC FIRST Bank', risk_score:79.2, is_suspect:true, fraud_role:'Collector' },
  { account_id:'8642666611469255', holder_name:'Co-Collector B', bank_name:'IDBI Bank', risk_score:67.7, is_suspect:true, fraud_role:'Collector' },
  { account_id:'00869354051', holder_name:'Distributor Primary', bank_name:'IDFC FIRST Bank', risk_score:57.4, is_suspect:true, fraud_role:'Distributor' },
  { account_id:'17771917925', holder_name:'Layerer Hub', bank_name:'IDFC FIRST Bank', risk_score:46.2, is_suspect:false, fraud_role:'Distributor' },
]

/* ─────────── Helper components ─────────── */

function riskTier(score: number): { label: string; cls: string; color: string } {
  if (score >= 75) return { label:'CRITICAL', cls:'badge-critical', color:'#ef4444' }
  if (score >= 50) return { label:'HIGH',     cls:'badge-high',     color:'#f97316' }
  if (score >= 25) return { label:'MEDIUM',   cls:'badge-medium',   color:'#f59e0b' }
  return { label:'LOW', cls:'badge-low', color:'#22d3ee' }
}

function formatINR(v: number): string {
  if (v >= 10_000_000) return `₹${(v/10_000_000).toFixed(2)}Cr`
  if (v >= 100_000)    return `₹${(v/100_000).toFixed(2)}L`
  return `₹${v.toLocaleString('en-IN')}`
}

function spark(n=12, seed=50): {v:number}[] {
  let val = seed
  return Array.from({length:n},()=>{ val=Math.max(5,val+(Math.random()-0.45)*14); return {v:val} })
}

/* ─────────── Sparkline ─────────── */
const Spark = ({data, color}: {data:{v:number}[], color:string}) => (
  <ResponsiveContainer width="100%" height="100%">
    <AreaChart data={data}>
      <defs>
        <linearGradient id={`sg-${color.replace('#','')}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.2}/>
          <stop offset="100%" stopColor={color} stopOpacity={0}/>
        </linearGradient>
      </defs>
      <Area dataKey="v" stroke={color} strokeWidth={1.5}
        fill={`url(#sg-${color.replace('#','')})`} dot={false} isAnimationActive={false}/>
    </AreaChart>
  </ResponsiveContainer>
)

/* ─────────── Main App ─────────── */
export default function App() {
  /* Navigation */
  const [view, setView] = useState<'dash'|'intel'|'ai'|'upload'>('dash')
  const [selAcct, setSelAcct] = useState<Account|null>(null)
  const [wsTab, setWsTab] = useState<'overview'|'ledger'|'graph'|'ai'>('overview')

  /* Data */
  const [stats, setStats] = useState<Stats>(MOCK_STATS)
  const [accounts, setAccounts] = useState<Account[]>(MOCK_ACCOUNTS)
  const [rings, setRings] = useState<Ring[]>(MOCK_RINGS)
  const [txs, setTxs] = useState<Tx[]>([])
  const [cps, setCps] = useState<Account[]>([])

  /* Filters */
  const [q, setQ] = useState('')
  const [suspectOnly, setSuspectOnly] = useState(false)
  const [minRisk, setMinRisk] = useState(0)
  const [bankF, setBankF] = useState('')
  const [page, setPage] = useState(1)
  const [totalPg, setTotalPg] = useState(1)

  /* Graph */
  const [nodes, setNodes] = useState<ForceNode[]>([])
  const [links, setLinks] = useState<ForceLink[]>([])
  const [gZoom, setGZoom] = useState(1)
  const [gPan, setGPan] = useState({x:0, y:0})
  const [hovNode, setHovNode] = useState<ForceNode|null>(null)
  const isDragging = useRef(false)
  const dragStart = useRef({x:0, y:0})

  /* AI */
  const [msgs, setMsgs] = useState<ChatMsg[]>([{
    sender:'ai', ts:Date.now(),
    text:'CIDECODE intelligence engine online. Phase 8 analytics loaded — 13 communities, 14,484 transactions indexed.\n\nI can build money trails, draft Suspicious Activity Reports, explain risk scores, or map fraud ring structures. How can I assist this investigation?'
  }])
  const [aiInput, setAiInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamTxt, setStreamTxt] = useState('')
  const [notes, setNotes] = useState('')
  const chatEnd = useRef<HTMLDivElement>(null)

  /* Upload */
  const [upFiles, setUpFiles] = useState<File[]>([])
  const [upStatus, setUpStatus] = useState<'idle'|'uploading'|'done'|'error'>('idle')
  const [upResults, setUpResults] = useState<any>(null)

  /* System */
  const [offline, setOffline] = useState(true)
  const [cmd, setCmd] = useState(false)
  const [cmdQ, setCmdQ] = useState('')
  const [onboarding, setOnboarding] = useState(false)

  /* Spark data — computed once */
  const sparkRisk  = useMemo(()=>spark(12,492),[])
  const sparkTxns  = useMemo(()=>spark(12,144),[])
  const sparkSusp  = useMemo(()=>spark(12,60),[])
  const sparkRings = useMemo(()=>spark(12,130),[])

  /* ── Boot ── */
  useEffect(()=>{
    if(!localStorage.getItem('cid_v')) { setOnboarding(true); localStorage.setItem('cid_v','1') }
    const k=(e:KeyboardEvent)=>{
      if((e.ctrlKey||e.metaKey)&&e.key==='k'){ e.preventDefault(); setCmd(p=>!p) }
      if(e.key==='Escape'){ setCmd(false) }
    }
    window.addEventListener('keydown', k)
    return ()=>window.removeEventListener('keydown', k)
  },[])

  useEffect(()=>{
    ;(async()=>{
      try {
        const r=await fetch(`${API_BASE}/dashboard/stats`)
        if(r.ok){ setStats(await r.json()); setOffline(false) }
        const r2=await fetch(`${API_BASE}/rings/`)
        if(r2.ok) setRings(await r2.json())
      } catch { setOffline(true) }
    })()
  },[])

  /* ── Account fetch ── */
  useEffect(()=>{
    if(offline){
      let f=MOCK_ACCOUNTS.filter(a=>{
        const ms=a.account_id.includes(q)||a.holder_name.toLowerCase().includes(q.toLowerCase())
        return ms&&(!suspectOnly||a.is_suspect)&&a.risk_score>=minRisk&&(!bankF||a.bank_name===bankF)
      })
      setAccounts(f); setTotalPg(1); return
    }
    ;(async()=>{
      const p=new URLSearchParams({ page:page.toString(), page_size:'12', min_risk_score:minRisk.toString() })
      if(q) p.append('search',q); if(suspectOnly) p.append('is_suspect','true'); if(bankF) p.append('bank_name',bankF)
      try {
        const r=await fetch(`${API_BASE}/accounts/?${p}`)
        if(r.ok){ const d=await r.json(); setAccounts(d.items); setTotalPg(Math.ceil(d.total/12)) }
      } catch {}
    })()
  },[q, suspectOnly, minRisk, bankF, page, offline])

  /* ── Account detail fetch ── */
  useEffect(()=>{
    if(!selAcct){ return }
    if(offline){ setTxs(MOCK_TXS); setCps(MOCK_CPS); return }
    ;(async()=>{
      try {
        const [r1,r2]=await Promise.all([
          fetch(`${API_BASE}/accounts/${selAcct.account_id}/transactions?page_size=50`),
          fetch(`${API_BASE}/accounts/${selAcct.account_id}/counterparties`)
        ])
        if(r1.ok){ const d=await r1.json(); setTxs(d.items) }
        if(r2.ok) setCps(await r2.json())
      } catch {}
    })()
  },[selAcct, offline])

  /* ── D3 Force ── */
  useEffect(()=>{
    if(!selAcct) return
    const center: ForceNode ={ id:selAcct.account_id, label:selAcct.holder_name||selAcct.account_id, bank:selAcct.bank_name, risk:selAcct.risk_score, type:'target', fx:400, fy:260 }
    const cpNodes: ForceNode[]=cps.map((c,i)=>({ id:c.account_id, label:c.holder_name||c.account_id, bank:c.bank_name, risk:c.risk_score, type:i%2===0?'receiver':'sender' }))
    const all=[center,...cpNodes]
    const lks: ForceLink[]=cps.map((c,i)=>{
      const out=i%2===0
      return { source:out?center.id:c.account_id, target:out?c.account_id:center.id, amount:50000*(i+1) }
    })
    setNodes(all); setLinks(lks)
    const sim=d3.forceSimulation<ForceNode>(all)
      .force('link', d3.forceLink<ForceNode,ForceLink>(lks).id(d=>d.id).distance(170))
      .force('charge', d3.forceManyBody().strength(-320))
      .force('center', d3.forceCenter(400,260))
      .force('collision', d3.forceCollide(28))
    sim.on('tick',()=>{ setNodes([...all]); setLinks([...lks]) })
    return ()=>{ sim.stop() }
  },[selAcct, cps])

  /* ── Scroll chat ── */
  useEffect(()=>{ chatEnd.current?.scrollIntoView({behavior:'smooth'}) },[msgs, streamTxt])

  /* ── Graph pan ── */
  const onSvgDown=(e:React.MouseEvent)=>{ isDragging.current=true; dragStart.current={x:e.clientX-gPan.x, y:e.clientY-gPan.y} }
  const onSvgMove=(e:React.MouseEvent)=>{ if(!isDragging.current) return; setGPan({x:e.clientX-dragStart.current.x, y:e.clientY-dragStart.current.y}) }
  const onSvgUp=()=>{ isDragging.current=false }

  /* ── AI response ── */
  const sendAI=useCallback((text:string)=>{
    setMsgs(p=>[...p,{sender:'user',text,ts:Date.now()}])
    setStreaming(true); setStreamTxt('')
    let reply='', sources=['analytics_summary.csv']
    const tl=text.toLowerCase()
    if(tl.includes('sar')||tl.includes('suspicious')){
      reply=`SUSPICIOUS ACTIVITY REPORT — DRAFT\n\nSubject: ${selAcct?.account_id||'[Account]'}\nGenerated: ${new Date().toLocaleString('en-IN')}\nClassification: CONFIDENTIAL\n\n─ TYPOLOGY IDENTIFIED ─\n• Structuring / Smurfing (High frequency sub-threshold deposits)\n• Round-Trip (Intensity: 0.77 — funds returned to originating entities)\n• Fan-In / Fan-Out (14 inbound, 5 outbound counterparties)\n\n─ NARRATIVE ─\nBetween February and June 2026, the subject account received ₹14,20,296 from 14 distinct mule accounts in structured batches of ₹10,000–₹15,000. More than 90% of received funds were swept within 2–4 hours via IMPS/RTGS to 5 layering beneficiaries. Three balance continuity breaches recorded.\n\n─ RECOMMENDATION ─\n1. Freeze outgoing transactions immediately\n2. Issue production warrant to ${selAcct?.bank_name||'the bank'}\n3. Escalate to CID Karnataka Cyber Crime Hub\n4. File with FIU-India within 7 working days`
      sources=['risk_scores.csv','cleaned_transactions.csv','money_trail.json']
    } else if(tl.includes('acco')||tl.includes('risk')||tl.includes('why')){
      const a=selAcct||MOCK_ACCOUNTS[0]
      reply=`RISK ASSESSMENT — ${a.account_id}\n\nOverall Score: ${a.risk_score}/100 (${riskTier(a.risk_score).label})\nFraud Role: ${a.fraud_role||'Collector'}\nCommunity: Louvain Partition #${a.fraud_ring_id||'1'}\n\n─ RISK DRIVERS ─\n• Collector Signature: Fan-in ratio 14:5 (14 inbound, 5 outbound)\n• Velocity Spike: Balance sweeps within 2–4 hours of credit\n• Anomaly Score: Isolation Forest flagged 3 transactions as extreme outliers (max: ${a.isolation_max_score?.toFixed(4)||'0.8699'})\n• Round-Trip: Circular flow detected at 0.77 intensity\n• Smurfing: 94.1% probability of structured sub-threshold deposits\n\n─ CONFIDENCE ─\nAll three model layers (Isolation Forest + XGBoost + Graph centrality) agree. This account is the primary collection node for Community #${a.fraud_ring_id||'1'}.`
      sources=['risk_scores.csv','community_summary.csv']
    } else if(tl.includes('trail')||tl.includes('flow')||tl.includes('money')){
      reply=`MONEY TRAIL ANALYSIS\n\nStarting node: ${selAcct?.account_id||'[Select an account]'}\n\n─ 2-HOP FUND TRACE ─\nHop 1 → 4 direct counterparties\n  • 18306700003 (IDFC FIRST — Collector, ₹4.2L total)\n  • 8642666611469255 (IDBI — Collector, ₹3.1L total)\n  • 00869354051 (IDFC FIRST — Distributor, ₹2.8L total)\n  • 17771917925 (IDFC FIRST — Distributor, ₹1.9L total)\n\nHop 2 → 12 secondary nodes detected\n  Total network exposure: ₹24.7L\n\n─ ROUND-TRIP PATHS ─\n2 circular flows detected:\n  A → 18306700003 → 00869354051 → A (6h cycle)\n  A → 8642666611469255 → [unresolved] → A (11h cycle)\n\nRecommendation: Request bank statements for all Hop-2 nodes.`
      sources=['money_trail.json','round_trip_patterns.csv']
    } else {
      reply=`COMMUNITY INTELLIGENCE SUMMARY\n\nLouvain algorithm identified 13 distinct communities from 14,484 transactions.\n\n─ TOP COMMUNITIES ─\nRing #7  — 7 accounts, ₹3.2L total flow, confidence: 0.90\nRing #1  — 4 accounts, ₹1.25L total flow, confidence: 0.85\nRing #11 — 6 accounts, ₹2.15L total flow, confidence: 0.88\nRing #8  — 5 accounts, ₹1.80L total flow, confidence: 0.75\nRing #10 — 3 accounts, ₹0.94L total flow, confidence: 0.80\n\nPrincipal concentration: IDFC FIRST Bank accounts appear in 8 of 13 communities, suggesting a coordinated network using a common banking channel.\n\nType a specific community number or account ID for deeper analysis.`
      sources=['community_summary.csv','louvain_memberships.csv']
    }
    let i=0
    const iv=setInterval(()=>{
      if(i<reply.length){ setStreamTxt(p=>p+reply[i]); i++ }
      else {
        clearInterval(iv)
        setMsgs(p=>[...p,{sender:'ai',text:reply,sources,ts:Date.now()}])
        setStreamTxt(''); setStreaming(false)
      }
    }, 12)
  },[selAcct])

  const onSend=(e:React.FormEvent)=>{ e.preventDefault(); if(!aiInput.trim()) return; sendAI(aiInput); setAiInput('') }

  /* ── Upload ── */
  const onDrop=(e:React.DragEvent)=>{ e.preventDefault(); setUpFiles(p=>[...p,...Array.from(e.dataTransfer.files)]) }
  const onFilePick=(e:React.ChangeEvent<HTMLInputElement>)=>{ if(e.target.files) setUpFiles(p=>[...p,...Array.from(e.target.files!)]) }
  const onUpload=async(e:React.FormEvent)=>{
    e.preventDefault(); if(!upFiles.length) return; setUpStatus('uploading')
    const fd=new FormData(); upFiles.forEach(f=>fd.append('files',f))
    try {
      const r=await fetch(`${API_BASE}/upload/`,{method:'POST',body:fd})
      if(r.ok){ setUpResults(await r.json()); setUpStatus('done') } else setUpStatus('error')
    } catch { setUpStatus('error') }
  }

  /* ── Filter cmd accounts ── */
  const cmdAccts=useMemo(()=>!cmdQ?[]:MOCK_ACCOUNTS.filter(a=>
    a.account_id.includes(cmdQ)||a.holder_name.toLowerCase().includes(cmdQ.toLowerCase())
  ),[cmdQ])

  /* ── Render ── */
  const RING_COLORS = ['#3b82f6','#8b5cf6','#ec4899','#f59e0b','#10b981']

  return (
    <div className="scanlines select-none" style={{
      display:'flex', flexDirection:'column', height:'100vh', overflow:'hidden',
      background:'var(--bg-void)', color:'var(--text-primary)', fontFamily:'var(--font-ui)'
    }}>

      {/* ═══ TOPBAR ═══ */}
      <header style={{
        height:52, display:'flex', alignItems:'center', justifyContent:'space-between',
        padding:'0 20px', borderBottom:'1px solid var(--border-dim)',
        background:'rgba(3,5,12,0.95)', backdropFilter:'blur(24px)', flexShrink:0, zIndex:50
      }}>
        {/* Brand */}
        <div style={{display:'flex', alignItems:'center', gap:10}}>
          <div style={{width:32, height:32, borderRadius:6, background:'rgba(59,130,246,0.1)', border:'1px solid rgba(59,130,246,0.25)', display:'flex', alignItems:'center', justifyContent:'center'}}>
            <Crosshair size={16} color="#3b82f6"/>
          </div>
          <div>
            <div style={{fontSize:13, fontWeight:800, letterSpacing:'0.08em', background:'linear-gradient(135deg,#60a5fa,#e0e7ff,#a78bfa)', WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent'}}>CIDECODE</div>
            <div className="label-xs" style={{color:'var(--text-dim)', letterSpacing:'0.15em'}}>CID INTELLIGENCE HUB</div>
          </div>
        </div>

        {/* Global search */}
        <div onClick={()=>setCmd(true)} style={{
          display:'flex', alignItems:'center', gap:8, padding:'6px 12px', borderRadius:6,
          background:'var(--bg-surface)', border:'1px solid var(--border-dim)',
          cursor:'pointer', width:280, transition:'border-color 0.15s'
        }}
          onMouseEnter={e=>(e.currentTarget.style.borderColor='var(--border-mid)')}
          onMouseLeave={e=>(e.currentTarget.style.borderColor='var(--border-dim)')}>
          <SearchIcon size={13} color="var(--text-muted)"/>
          <span style={{fontSize:12, color:'var(--text-muted)', flex:1}}>Search accounts, IDs, banks…</span>
          <kbd style={{fontSize:10, padding:'1px 5px', borderRadius:3, background:'var(--bg-raised)', border:'1px solid var(--border-mid)', color:'var(--text-dim)', fontFamily:'var(--font-mono)'}}>⌘K</kbd>
        </div>

        {/* Status + live graph button */}
        <div style={{display:'flex', alignItems:'center', gap:12}}>
          <div style={{display:'flex', alignItems:'center', gap:6, padding:'4px 10px', borderRadius:4, background:'var(--bg-surface)', border:'1px solid var(--border-dim)'}}>
            <span className={offline?'status-sim':'status-live'} style={{width:6, height:6, borderRadius:'50%', display:'inline-block'}}/>
            <span style={{fontSize:10, fontWeight:600, letterSpacing:'0.08em', color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>
              {offline ? 'SIMULATION' : 'LIVE PIPELINE'}
            </span>
          </div>
        </div>
      </header>

      {/* ═══ BODY ═══ */}
      <div style={{display:'flex', flex:1, overflow:'hidden'}}>

        {/* ─── Sidebar ─── */}
        <nav style={{
          width:52, display:'flex', flexDirection:'column', alignItems:'center',
          padding:'16px 0', gap:4, borderRight:'1px solid var(--border-dim)',
          background:'rgba(4,6,14,0.6)', flexShrink:0
        }}>
          {([
            {id:'dash',  Icon:Activity,    tip:'Intelligence Overview'},
            {id:'intel', Icon:Users,       tip:'Investigation Center'},
            {id:'ai',    Icon:MessageSquare,tip:'AI Copilot'},
            {id:'upload',Icon:UploadCloud, tip:'Statement Ingestion'},
          ] as const).map(({id,Icon,tip})=>(
            <button key={id} onClick={()=>setView(id)} title={tip} style={{
              width:40, height:40, borderRadius:6, display:'flex', alignItems:'center', justifyContent:'center',
              background: view===id ? 'var(--action-dim)' : 'transparent',
              border: `1px solid ${view===id ? 'rgba(59,130,246,0.3)' : 'transparent'}`,
              cursor:'pointer', transition:'all 0.15s', color: view===id ? 'var(--action)' : 'var(--text-muted)'
            }}>
              <Icon size={16}/>
            </button>
          ))}
        </nav>

        {/* ─── Main content ─── */}
        <main style={{flex:1, overflow:'hidden', display:'flex', flexDirection:'column'}}>
          <AnimatePresence mode="wait">

            {/* ═══ DASH VIEW ═══ */}
            {view==='dash' && (
              <motion.div key="dash" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
                style={{flex:1, overflow:'auto', padding:24, display:'flex', flexDirection:'column', gap:20}}>

                {/* Header */}
                <div style={{display:'flex', alignItems:'center', justifyContent:'space-between'}}>
                  <div>
                    <h1 style={{fontSize:20, fontWeight:800, letterSpacing:'-0.02em'}}>Intelligence Overview</h1>
                    <p style={{fontSize:11, color:'var(--text-muted)', marginTop:3}}>Louvain community analysis · Isolation Forest scoring · Phase 8 analytics</p>
                  </div>
                  <button onClick={()=>{ setSelAcct(MOCK_ACCOUNTS[0]); setWsTab('overview'); setView('intel') }} style={{
                    display:'flex', alignItems:'center', gap:6, padding:'7px 14px', borderRadius:6,
                    background:'var(--action)', border:'none', cursor:'pointer', fontSize:11, fontWeight:700, color:'#fff'
                  }}>
                    <Eye size={13}/> Open Investigation
                  </button>
                </div>

                {/* KPI strip */}
                <div style={{display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:14}}>
                  {[
                    { label:'Amount at Risk',   value: formatINR(stats.total_amount_at_risk), spark:sparkRisk,  color:'#ef4444', Icon:TrendingUp },
                    { label:'Suspect Profiles', value:`${stats.suspect_accounts} / ${stats.total_accounts}`, spark:sparkSusp,  color:'#f97316', Icon:ShieldAlert },
                    { label:'Transactions Indexed', value:stats.total_transactions.toLocaleString(), spark:sparkTxns,  color:'#3b82f6', Icon:ArrowRightLeft },
                    { label:'Fraud Communities', value:`${stats.fraud_rings_detected}`, spark:sparkRings, color:'#8b5cf6', Icon:Layers },
                  ].map(({label,value,spark,color,Icon})=>(
                    <div key={label} className="kpi-card">
                      <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start'}}>
                        <div>
                          <div className="label-xs">{label}</div>
                          <div style={{fontSize:22, fontWeight:800, marginTop:6, fontFamily:'var(--font-mono)', letterSpacing:'-0.03em', color:'var(--text-primary)'}}>{value}</div>
                        </div>
                        <div style={{width:32, height:32, borderRadius:6, background:`${color}14`, border:`1px solid ${color}30`, display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0}}>
                          <Icon size={14} color={color}/>
                        </div>
                      </div>
                      <div style={{height:36, marginTop:12}}>
                        <Spark data={spark.map(s=>({v:s.v}))} color={color}/>
                      </div>
                    </div>
                  ))}
                </div>

                {/* Main grid */}
                <div style={{display:'grid', gridTemplateColumns:'1fr 340px', gap:14, flex:1, minHeight:0}}>

                  {/* Ring heatmap */}
                  <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, padding:20, display:'flex', flexDirection:'column', gap:14}}>
                    <div style={{display:'flex', alignItems:'center', gap:8}}>
                      <Network size={14} color="#3b82f6"/>
                      <span style={{fontSize:12, fontWeight:700}}>Louvain Community Flow Heatmap</span>
                    </div>
                    <div style={{flex:1, minHeight:220}}>
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={rings.map(r=>({ name:`Ring #${r.ring_id}`, flow:r.total_amount_moved, accts:r.total_accounts, conf:Math.round(r.confidence_score*100) }))} barSize={28}>
                          <defs>
                            {rings.map((r,i)=>(
                              <linearGradient key={r.ring_id} id={`bg-${r.ring_id}`} x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={RING_COLORS[i%RING_COLORS.length]} stopOpacity={0.9}/>
                                <stop offset="100%" stopColor={RING_COLORS[i%RING_COLORS.length]} stopOpacity={0.4}/>
                              </linearGradient>
                            ))}
                          </defs>
                          <XAxis dataKey="name" stroke="var(--text-dim)" fontSize={10} tickLine={false} axisLine={false} fontFamily="var(--font-mono)"/>
                          <YAxis stroke="var(--text-dim)" fontSize={10} tickLine={false} axisLine={false} tickFormatter={v=>`₹${(v/1000).toFixed(0)}K`}/>
                          <ReTooltip
                            contentStyle={{background:'var(--bg-raised)', border:'1px solid var(--border-mid)', borderRadius:6, fontSize:11, fontFamily:'var(--font-mono)'}}
                            formatter={(v:number)=>[`₹${v.toLocaleString('en-IN')}`,'Flow']}/>
                          <Bar dataKey="flow" radius={[4,4,0,0]}>
                            {rings.map((r,i)=>(
                              <Cell key={r.ring_id} fill={`url(#bg-${r.ring_id})`}/>
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                    {/* Ring legend */}
                    <div style={{display:'flex', gap:10, flexWrap:'wrap'}}>
                      {rings.map((r,i)=>(
                        <div key={r.ring_id} style={{display:'flex', alignItems:'center', gap:5, cursor:'pointer'}}
                          onClick={()=>{ const a=MOCK_ACCOUNTS.find(a=>a.fraud_ring_id===r.ring_id); if(a){setSelAcct(a);setWsTab('graph');setView('intel')} }}>
                          <span style={{width:8, height:8, borderRadius:2, background:RING_COLORS[i%RING_COLORS.length]}}/>
                          <span style={{fontSize:10, color:'var(--text-secondary)', fontFamily:'var(--font-mono)'}}>Ring #{r.ring_id}</span>
                          <span style={{fontSize:10, color:'var(--text-muted)'}}>{r.total_accounts}a · {Math.round(r.confidence_score*100)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Investigation queue */}
                  <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, padding:16, display:'flex', flexDirection:'column', gap:10, overflow:'hidden'}}>
                    <div style={{display:'flex', alignItems:'center', gap:8, flexShrink:0}}>
                      <ShieldAlert size={13} color="#ef4444"/>
                      <span style={{fontSize:11, fontWeight:700}}>Priority Queue</span>
                      <span style={{fontSize:10, marginLeft:'auto', color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>by risk score ↓</span>
                    </div>
                    <div style={{overflow:'auto', flex:1}}>
                      {MOCK_ACCOUNTS.map(a=>{
                        const t=riskTier(a.risk_score)
                        return (
                          <div key={a.account_id} className="account-card" style={{marginBottom:6}}
                            onClick={()=>{ setSelAcct(a); setWsTab('overview'); setView('intel') }}>
                            <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start'}}>
                              <span style={{fontSize:11, fontWeight:700, fontFamily:'var(--font-mono)', color:'var(--text-primary)'}}>{a.account_id}</span>
                              <span className={`badge-${t.label.toLowerCase()} flag-chip`} style={{fontSize:9}}>{t.label}</span>
                            </div>
                            <div style={{fontSize:10, color:'var(--text-muted)', marginTop:5, display:'flex', alignItems:'center', gap:6}}>
                              <span>{a.bank_name}</span>
                              <span style={{width:2, height:2, borderRadius:'50%', background:'var(--text-dim)'}}/>
                              <span>{a.fraud_role}</span>
                              <span style={{marginLeft:'auto', fontSize:11, fontWeight:800, fontFamily:'var(--font-mono)', color:t.color}}>{a.risk_score.toFixed(1)}</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </div>
              </motion.div>
            )}

            {/* ═══ INTEL VIEW ═══ */}
            {view==='intel' && (
              <motion.div key="intel" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
                style={{flex:1, overflow:'hidden', display:'flex', flexDirection:'column'}}>

                {/* Filter bar */}
                <div style={{padding:'10px 20px', borderBottom:'1px solid var(--border-dim)', display:'flex', alignItems:'center', gap:10, flexShrink:0, background:'rgba(4,6,14,0.5)'}}>
                  <div style={{position:'relative', flex:1, maxWidth:320}}>
                    <SearchIcon size={13} style={{position:'absolute', left:10, top:'50%', transform:'translateY(-50%)', color:'var(--text-muted)'}}/>
                    <input placeholder="Search account IDs or holders…" value={q} onChange={e=>setQ(e.target.value)} style={{
                      width:'100%', paddingLeft:30, paddingRight:10, height:32,
                      background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:5,
                      color:'var(--text-primary)', fontSize:12, outline:'none', fontFamily:'var(--font-ui)'
                    }}/>
                  </div>
                  <select value={bankF} onChange={e=>setBankF(e.target.value)} style={{
                    height:32, padding:'0 8px', background:'var(--bg-surface)', border:'1px solid var(--border-dim)',
                    borderRadius:5, color:'var(--text-secondary)', fontSize:12, outline:'none', cursor:'pointer'
                  }}>
                    <option value="">All Banks</option>
                    {stats.banks_covered.map(b=><option key={b} value={b}>{b}</option>)}
                  </select>
                  <label style={{display:'flex', alignItems:'center', gap:6, fontSize:11, color:'var(--text-muted)', cursor:'pointer', userSelect:'none'}}>
                    <input type="checkbox" checked={suspectOnly} onChange={e=>setSuspectOnly(e.target.checked)} style={{accentColor:'var(--action)'}}/>
                    Suspects only
                  </label>
                  <div style={{display:'flex', alignItems:'center', gap:8, fontSize:11, color:'var(--text-muted)'}}>
                    <span style={{whiteSpace:'nowrap'}}>Min Risk: <strong style={{color:'var(--text-primary)', fontFamily:'var(--font-mono)'}}>{minRisk}</strong></span>
                    <input type="range" min={0} max={100} value={minRisk} onChange={e=>setMinRisk(Number(e.target.value))} style={{accentColor:'var(--action)', width:80}}/>
                  </div>
                </div>

                {/* Split pane */}
                <div style={{flex:1, overflow:'hidden', display:'flex'}}>

                  {/* Left: account list */}
                  <div className="pane-left" style={{width:280, padding:12, borderRight:'1px solid var(--border-dim)', background:'rgba(4,6,14,0.4)', display:'flex', flexDirection:'column', gap:6}}>
                    <div className="label-xs" style={{padding:'4px 4px 8px', borderBottom:'1px solid var(--border-dim)', marginBottom:2}}>TARGETS — {accounts.length} accounts</div>
                    {accounts.map(a=>{
                      const t=riskTier(a.risk_score)
                      return (
                        <div key={a.account_id}
                          className={`account-card${selAcct?.account_id===a.account_id?' selected':''}`}
                          onClick={()=>{ setSelAcct(a); setWsTab('overview') }}>
                          <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:8}}>
                            <span style={{fontSize:11, fontWeight:700, fontFamily:'var(--font-mono)', flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{a.account_id}</span>
                            <span className={`flag-chip badge-${t.label.toLowerCase()}`} style={{flexShrink:0}}>{a.risk_score.toFixed(0)}</span>
                          </div>
                          <div style={{fontSize:10, color:'var(--text-muted)', marginTop:4, display:'flex', gap:6}}>
                            <span>{a.bank_name.replace(' Bank','')}</span>
                            <span style={{color:'var(--text-dim)'}}>·</span>
                            <span>{a.fraud_role||'Unknown'}</span>
                          </div>
                        </div>
                      )
                    })}
                    {/* Pagination */}
                    <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', padding:'8px 0 0', borderTop:'1px solid var(--border-dim)', marginTop:'auto', flexShrink:0}}>
                      <span style={{fontSize:10, color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>Pg {page}/{totalPg}</span>
                      <div style={{display:'flex', gap:4}}>
                        {[{Icon:ChevronLeft,fn:()=>setPage(p=>Math.max(1,p-1)),dis:page===1},{Icon:ChevronRight,fn:()=>setPage(p=>p+1),dis:page===totalPg}].map(({Icon,fn,dis})=>(
                          <button key={Icon.name} onClick={fn} disabled={dis} style={{
                            width:24, height:24, borderRadius:4, background:'var(--bg-surface)',
                            border:'1px solid var(--border-dim)', cursor:dis?'not-allowed':'pointer',
                            display:'flex', alignItems:'center', justifyContent:'center', opacity:dis?0.3:1, color:'var(--text-secondary)'
                          }}>
                            <Icon size={12}/>
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Right: workspace */}
                  <div className="pane-right" style={{padding:'0', display:'flex', flexDirection:'column'}}>
                    {selAcct ? (
                      <>
                        {/* Workspace tab bar */}
                        <div style={{display:'flex', borderBottom:'1px solid var(--border-dim)', padding:'0 20px', flexShrink:0, background:'rgba(4,6,14,0.5)'}}>
                          {([
                            {id:'overview',  label:'Overview'},
                            {id:'ledger',    label:'Statement Ledger'},
                            {id:'graph',     label:'Money Trail'},
                            {id:'ai',        label:'AI Case File'},
                          ] as const).map(({id,label})=>(
                            <button key={id} className={`workspace-tab${wsTab===id?' active':''}`} onClick={()=>setWsTab(id)}>{label}</button>
                          ))}
                          {/* Generate AI summary shortcut */}
                          <button onClick={()=>{ setView('ai'); sendAI(`Generate full case analysis for ${selAcct.account_id}`) }} style={{
                            marginLeft:'auto', alignSelf:'center', display:'flex', alignItems:'center', gap:5,
                            padding:'5px 12px', borderRadius:5, background:'var(--action-dim)', border:'1px solid var(--border-blue)',
                            cursor:'pointer', fontSize:10, fontWeight:700, color:'var(--action)'
                          }}>
                            <Sparkles size={11}/> Generate AI Summary
                          </button>
                        </div>

                        {/* Workspace body */}
                        <div style={{flex:1, overflow:'auto', padding:20}}>
                          <AnimatePresence mode="wait">

                            {/* OVERVIEW */}
                            {wsTab==='overview' && (
                              <motion.div key="ov" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
                                style={{display:'flex', flexDirection:'column', gap:16}}>
                                {/* Account header */}
                                <div style={{display:'flex', justifyContent:'space-between', alignItems:'flex-start'}}>
                                  <div>
                                    <div className="label-xs">SUBJECT ACCOUNT</div>
                                    <h2 style={{fontSize:18, fontWeight:800, fontFamily:'var(--font-mono)', marginTop:4, letterSpacing:'-0.02em'}}>{selAcct.account_id}</h2>
                                    <div style={{fontSize:11, color:'var(--text-muted)', marginTop:4}}>{selAcct.bank_name} · Louvain Community #{selAcct.fraud_ring_id||'—'}</div>
                                  </div>
                                  <div style={{display:'flex', gap:8}}>
                                    <span className={`flag-chip badge-${riskTier(selAcct.risk_score).label.toLowerCase()}`}>{selAcct.fraud_role}</span>
                                    <button onClick={()=>window.open(`${API_BASE}/accounts/${selAcct.account_id}/transactions`)} style={{
                                      display:'flex', alignItems:'center', gap:5, padding:'6px 10px', borderRadius:5,
                                      background:'var(--bg-surface)', border:'1px solid var(--border-dim)', cursor:'pointer',
                                      fontSize:11, color:'var(--text-secondary)'
                                    }}>
                                      <Download size={12}/> Export CSV
                                    </button>
                                  </div>
                                </div>

                                {/* Stats grid */}
                                <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12}}>
                                  {/* Risk score */}
                                  <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, padding:16}}>
                                    <div className="label-xs" style={{marginBottom:8}}>COMPOSITE RISK SCORE</div>
                                    <div style={{display:'flex', alignItems:'baseline', gap:8}}>
                                      <span style={{fontSize:40, fontWeight:900, fontFamily:'var(--font-mono)', color:riskTier(selAcct.risk_score).color, lineHeight:1}}>{selAcct.risk_score.toFixed(1)}</span>
                                      <span style={{fontSize:18, color:'var(--text-dim)'}}>/100</span>
                                    </div>
                                    {/* Score bar */}
                                    <div style={{height:4, borderRadius:2, background:'var(--bg-raised)', marginTop:10, overflow:'hidden'}}>
                                      <div style={{height:'100%', width:`${selAcct.risk_score}%`, background:riskTier(selAcct.risk_score).color, borderRadius:2, transition:'width 0.6s ease'}}/>
                                    </div>
                                    {selAcct.isolation_mean_score!==undefined && (
                                      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:10, marginTop:12, paddingTop:10, borderTop:'1px solid var(--border-dim)'}}>
                                        {[['IF Mean', selAcct.isolation_mean_score],['IF Max', selAcct.isolation_max_score]].map(([l,v])=>(
                                          <div key={String(l)}>
                                            <div className="label-xs">{String(l)}</div>
                                            <div style={{fontSize:13, fontWeight:700, fontFamily:'var(--font-mono)', color:'var(--text-primary)', marginTop:2}}>{Number(v).toFixed(4)}</div>
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                  {/* Meta */}
                                  <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, padding:16, display:'flex', flexDirection:'column', gap:10}}>
                                    {[
                                      ['Bank', selAcct.bank_name],
                                      ['Holder', selAcct.holder_name||'Unknown'],
                                      ['Fraud Role', selAcct.fraud_role||'—'],
                                      ['Community', `Louvain #${selAcct.fraud_ring_id||'—'}`],
                                    ].map(([l,v])=>(
                                      <div key={l} style={{display:'flex', justifyContent:'space-between', alignItems:'center', borderBottom:'1px solid var(--border-dim)', paddingBottom:8}}>
                                        <span className="label-xs">{l}</span>
                                        <span style={{fontSize:11, fontWeight:600, color:'var(--text-primary)'}}>{v}</span>
                                      </div>
                                    ))}
                                  </div>
                                </div>

                                {/* Evidence ledger */}
                                {selAcct.risk_reasoning && (
                                  <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, padding:16}}>
                                    <div className="label-xs" style={{marginBottom:8}}>EVIDENCE REASONING CHAIN</div>
                                    <div style={{fontSize:12, color:'var(--text-secondary)', lineHeight:1.7}}>
                                      {selAcct.risk_reasoning.split('|').map((s,i)=>(
                                        <div key={i} style={{display:'flex', gap:8, padding:'4px 0'}}>
                                          <span style={{color:'var(--threat-medium)', fontFamily:'var(--font-mono)', fontSize:11, flexShrink:0, marginTop:1}}>→</span>
                                          <span>{s.trim()}</span>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                {/* AML flag chips */}
                                {selAcct.active_patterns && (
                                  <div>
                                    <div className="label-xs" style={{marginBottom:8}}>ACTIVE AML FLAGS</div>
                                    <div style={{display:'flex', flexWrap:'wrap', gap:6}}>
                                      {selAcct.active_patterns.split('|').map(p=>(
                                        <span key={p} className="flag-chip" style={{
                                          color:'var(--threat-medium)', background:'rgba(245,158,11,0.08)',
                                          borderColor:'rgba(245,158,11,0.25)', fontSize:10
                                        }}>
                                          {p.trim()}
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                )}
                              </motion.div>
                            )}

                            {/* LEDGER */}
                            {wsTab==='ledger' && (
                              <motion.div key="ldr" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}>
                                <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14}}>
                                  <h3 style={{fontSize:13, fontWeight:700}}>Statement Ledger</h3>
                                  <span style={{fontSize:10, color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>{txs.length} transactions · last 50</span>
                                </div>
                                <div style={{overflow:'auto'}}>
                                  <table style={{width:'100%', borderCollapse:'collapse', fontSize:11}}>
                                    <thead>
                                      <tr style={{borderBottom:'1px solid var(--border-mid)'}}>
                                        {['Date / Time','Narration / UTR','Ch.','Debit (₹)','Credit (₹)','Balance (₹)','Flags'].map(h=>(
                                          <th key={h} className="label-xs" style={{textAlign:h.includes('₹')?'right':'left', padding:'8px 10px', fontWeight:700}}>{h}</th>
                                        ))}
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {txs.map(tx=>(
                                        <tr key={tx.id} className="tx-row">
                                          <td style={{padding:'8px 10px', whiteSpace:'nowrap', fontFamily:'var(--font-mono)', fontSize:10}}>
                                            <div style={{color:'var(--text-primary)'}}>{tx.date}</div>
                                            <div style={{color:'var(--text-dim)'}}>{tx.time}</div>
                                          </td>
                                          <td style={{padding:'8px 10px', maxWidth:220}}>
                                            <div style={{color:'var(--text-secondary)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{tx.narration}</div>
                                            <div style={{color:'var(--text-dim)', fontSize:9, fontFamily:'var(--font-mono)', marginTop:2}}>{tx.utr_ref}</div>
                                          </td>
                                          <td style={{padding:'8px 10px'}}>
                                            <span style={{fontSize:9, padding:'2px 5px', borderRadius:3, background:'var(--bg-raised)', border:'1px solid var(--border-dim)', fontFamily:'var(--font-mono)', color:'var(--text-muted)'}}>{tx.channel}</span>
                                          </td>
                                          <td style={{padding:'8px 10px', textAlign:'right', fontFamily:'var(--font-mono)', fontWeight:700, color:tx.debit>0?'#ef4444':'var(--text-dim)'}}>
                                            {tx.debit>0?tx.debit.toLocaleString('en-IN'):'—'}
                                          </td>
                                          <td style={{padding:'8px 10px', textAlign:'right', fontFamily:'var(--font-mono)', fontWeight:700, color:tx.credit>0?'#10b981':'var(--text-dim)'}}>
                                            {tx.credit>0?tx.credit.toLocaleString('en-IN'):'—'}
                                          </td>
                                          <td style={{padding:'8px 10px', textAlign:'right', fontFamily:'var(--font-mono)', color:tx.balance<0?'var(--threat-critical)':'var(--text-secondary)'}}>
                                            {tx.balance.toLocaleString('en-IN')}
                                          </td>
                                          <td style={{padding:'8px 10px', textAlign:'center'}}>
                                            <div style={{display:'flex', gap:4, justifyContent:'center'}}>
                                              {tx.is_high_value_flag && <span title="High Value Outlier" style={{width:7, height:7, borderRadius:'50%', background:'var(--threat-critical)', display:'inline-block'}}/>}
                                              {tx.is_balance_breach && <span title="Balance Breach" style={{width:7, height:7, borderRadius:'50%', background:'var(--threat-high)', display:'inline-block'}}/>}
                                              {!tx.is_high_value_flag&&!tx.is_balance_breach && <span style={{width:5, height:5, borderRadius:'50%', background:'var(--bg-overlay)', display:'inline-block'}}/>}
                                            </div>
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                              </motion.div>
                            )}

                            {/* GRAPH / MONEY TRAIL */}
                            {wsTab==='graph' && (
                              <motion.div key="gr" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
                                style={{display:'flex', flexDirection:'column', gap:12}}>
                                <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
                                  <div>
                                    <h3 style={{fontSize:13, fontWeight:700}}>Ego Money Trail</h3>
                                    <p style={{fontSize:10, color:'var(--text-muted)', marginTop:2}}>Force-directed network · drag nodes · scroll to zoom</p>
                                  </div>
                                  <div style={{display:'flex', gap:6}}>
                                    {[{Icon:Maximize2, fn:()=>setGZoom(z=>Math.min(3,z+0.15))},{Icon:Minimize2, fn:()=>setGZoom(z=>Math.max(0.4,z-0.15))}].map(({Icon,fn})=>(
                                      <button key={Icon.name} onClick={fn} style={{width:28,height:28,borderRadius:4,background:'var(--bg-surface)',border:'1px solid var(--border-dim)',cursor:'pointer',display:'flex',alignItems:'center',justifyContent:'center',color:'var(--text-muted)'}}>
                                        <Icon size={12}/>
                                      </button>
                                    ))}
                                    <button onClick={()=>{setGZoom(1);setGPan({x:0,y:0})}} style={{height:28,padding:'0 8px',borderRadius:4,background:'var(--bg-surface)',border:'1px solid var(--border-dim)',cursor:'pointer',fontSize:10,color:'var(--text-muted)'}}>RESET</button>
                                  </div>
                                </div>

                                <div className="graph-canvas-wrap" style={{height:400}}>
                                  <svg className="graph-canvas"
                                    onMouseDown={onSvgDown} onMouseMove={onSvgMove}
                                    onMouseUp={onSvgUp} onMouseLeave={onSvgUp}>

                                    {/* Arrow markers */}
                                    <defs>
                                      <marker id="arr-blue" markerWidth={6} markerHeight={6} refX={5} refY={3} orient="auto">
                                        <path d="M0,0 L6,3 L0,6 Z" fill="#3b82f6" opacity={0.8}/>
                                      </marker>
                                      <marker id="arr-orange" markerWidth={6} markerHeight={6} refX={5} refY={3} orient="auto">
                                        <path d="M0,0 L6,3 L0,6 Z" fill="#f97316" opacity={0.8}/>
                                      </marker>
                                    </defs>

                                    <g transform={`translate(${gPan.x},${gPan.y}) scale(${gZoom})`}>
                                      {/* Edges */}
                                      {links.map((lk,i)=>{
                                        const src=typeof lk.source==='string'?nodes.find(n=>n.id===lk.source):lk.source
                                        const tgt=typeof lk.target==='string'?nodes.find(n=>n.id===lk.target):lk.target
                                        if(!src||!tgt) return null
                                        const x1=src.x||0,y1=src.y||0,x2=tgt.x||0,y2=tgt.y||0
                                        const dr=Math.sqrt((x2-x1)**2+(y2-y1)**2)
                                        const d=`M${x1} ${y1} A${dr} ${dr} 0 0 1 ${x2} ${y2}`
                                        const isOut=src.id===selAcct.account_id
                                        const c=isOut?'#3b82f6':'#f97316'
                                        return (
                                          <g key={`e${i}`}>
                                            <path d={d} fill="none" stroke={c} strokeOpacity={0.15} strokeWidth={4}/>
                                            <path d={d} fill="none" stroke={c} strokeWidth={1.5}
                                              className={isOut?'edge-flow':'edge-flow-reverse'}
                                              strokeOpacity={0.8}
                                              markerEnd={`url(#arr-${isOut?'blue':'orange'})`}/>
                                            {/* Amount label */}
                                            <text fill={c} fontSize={9} fontFamily="var(--font-mono)" opacity={0.7}
                                              x={(x1+x2)/2} y={(y1+y2)/2-5} textAnchor="middle">
                                              {formatINR(lk.amount)}
                                            </text>
                                          </g>
                                        )
                                      })}

                                      {/* Nodes */}
                                      {nodes.map(nd=>{
                                        const cx=nd.x||0, cy=nd.y||0
                                        const isC=nd.type==='target'
                                        const t=riskTier(nd.risk)
                                        const r=isC?20:13
                                        return (
                                          <g key={nd.id} className="node-group"
                                            transform={`translate(${cx},${cy})`}
                                            onMouseEnter={()=>setHovNode(nd)}
                                            onMouseLeave={()=>setHovNode(null)}
                                            onClick={()=>{ const a=MOCK_ACCOUNTS.find(a=>a.account_id===nd.id); if(a) setSelAcct(a) }}>

                                            {/* Pulse ring for critical */}
                                            {isC && nd.risk>=75 && (
                                              <circle r={r} fill="none" stroke={t.color} strokeWidth={1} className="pulse-ring"/>
                                            )}

                                            {/* Node outer glow */}
                                            <circle r={r+3} fill="none" stroke={t.color} strokeOpacity={0.12} strokeWidth={3}/>

                                            <circle r={r} fill={isC?'#0d1425':'#080e1c'}
                                              stroke={t.color} strokeWidth={isC?2:1.5}
                                              className="node-circle" style={{color:t.color}}/>

                                            {/* Icon indicator */}
                                            {isC && (
                                              <text y={1} textAnchor="middle" dominantBaseline="middle"
                                                fontSize={10} fill={t.color}>⚠</text>
                                            )}

                                            <text y={r+10} textAnchor="middle" className="node-label" fontSize={9}>
                                              {nd.id.substring(0,11)}
                                            </text>
                                            <text y={r+20} textAnchor="middle" className="node-label" fontSize={8}>
                                              {nd.bank.replace(' Bank','').substring(0,10)}
                                            </text>
                                          </g>
                                        )
                                      })}
                                    </g>
                                  </svg>

                                  {/* Hover card */}
                                  {hovNode && (
                                    <div style={{
                                      position:'absolute', bottom:12, left:12,
                                      background:'rgba(7,11,21,0.97)', border:'1px solid var(--border-mid)',
                                      borderRadius:6, padding:'10px 12px', minWidth:200,
                                      boxShadow:'0 8px 24px rgba(0,0,0,0.5)', pointerEvents:'none'
                                    }}>
                                      <div style={{fontSize:11, fontWeight:700, fontFamily:'var(--font-mono)'}}>{hovNode.id}</div>
                                      <div style={{fontSize:10, color:'var(--text-muted)', marginTop:3}}>{hovNode.bank}</div>
                                      <div style={{display:'flex', justifyContent:'space-between', marginTop:8, paddingTop:6, borderTop:'1px solid var(--border-dim)'}}>
                                        <span className="label-xs">Risk Score</span>
                                        <span style={{fontSize:13, fontWeight:800, fontFamily:'var(--font-mono)', color:riskTier(hovNode.risk).color}}>{hovNode.risk.toFixed(1)}</span>
                                      </div>
                                    </div>
                                  )}

                                  {/* Legend */}
                                  <div style={{position:'absolute', top:10, right:10, display:'flex', flexDirection:'column', gap:5}}>
                                    {[['#3b82f6','Outflow'],['#f97316','Inflow']].map(([c,l])=>(
                                      <div key={l} style={{display:'flex', alignItems:'center', gap:5, fontSize:9, color:'var(--text-muted)', fontFamily:'var(--font-mono)'}}>
                                        <span style={{width:16, height:1.5, background:c, display:'inline-block'}}/>
                                        <span>{l}</span>
                                      </div>
                                    ))}
                                  </div>
                                </div>

                                {/* Counterparty list */}
                                <div>
                                  <div className="label-xs" style={{marginBottom:8}}>DIRECT COUNTERPARTIES</div>
                                  <div style={{display:'grid', gridTemplateColumns:'repeat(2,1fr)', gap:8}}>
                                    {cps.map(cp=>{
                                      const t=riskTier(cp.risk_score)
                                      return (
                                        <div key={cp.account_id} style={{
                                          padding:'10px 12px', borderRadius:6,
                                          background:'var(--bg-surface)', border:'1px solid var(--border-dim)',
                                          cursor:'pointer', display:'flex', justifyContent:'space-between', alignItems:'center'
                                        }} onClick={()=>setSelAcct(cp)}>
                                          <div>
                                            <div style={{fontSize:10, fontWeight:700, fontFamily:'var(--font-mono)'}}>{cp.account_id}</div>
                                            <div style={{fontSize:9, color:'var(--text-muted)', marginTop:3}}>{cp.bank_name} · {cp.fraud_role}</div>
                                          </div>
                                          <div style={{fontSize:13, fontWeight:800, fontFamily:'var(--font-mono)', color:t.color}}>{cp.risk_score.toFixed(0)}</div>
                                        </div>
                                      )
                                    })}
                                  </div>
                                </div>
                              </motion.div>
                            )}

                            {/* AI CASE FILE */}
                            {wsTab==='ai' && (
                              <motion.div key="aicf" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
                                style={{display:'flex', flexDirection:'column', gap:16}}>
                                <div style={{display:'flex', alignItems:'center', gap:8}}>
                                  <Sparkles size={14} color="var(--action)"/>
                                  <h3 style={{fontSize:13, fontWeight:700}}>AI Investigator Diagnostics</h3>
                                </div>
                                {/* Auto-generated summary card */}
                                <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-blue)', borderRadius:8, padding:20}}>
                                  <div className="label-xs" style={{marginBottom:10, color:'var(--action)'}}>AUTOMATIC CASE STRUCTURING NARRATIVE</div>
                                  <p style={{fontSize:12, color:'var(--text-secondary)', lineHeight:1.7}}>
                                    Account <strong style={{fontFamily:'var(--font-mono)', color:'var(--text-primary)'}}>{selAcct.account_id}</strong> behaves as a high-density Star Collector node.
                                    Funds received originate from 14 shell profiles, showing structured credit velocities in near-identical batches of ₹10,000–₹15,000, a hallmark of Smurfing. Post-collection, 94%+ of funds are swept within 2–4 hours via IMPS/RTGS to 5 distribution nodes in the same Louvain Community #{selAcct.fraud_ring_id||'1'}.
                                  </p>
                                  <div style={{display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10, marginTop:16}}>
                                    {[['Layering Score','0.862 (High)','#f97316'],['Smurfing Prob.','94.1%','#ef4444'],['Round-Trip Intensity','0.77','#f59e0b']].map(([l,v,c])=>(
                                      <div key={l} style={{padding:'10px 12px', borderRadius:6, background:'var(--bg-base)', border:'1px solid var(--border-dim)'}}>
                                        <div className="label-xs">{l}</div>
                                        <div style={{fontSize:15, fontWeight:800, fontFamily:'var(--font-mono)', color:c, marginTop:5}}>{v}</div>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                                {/* Deep dive buttons */}
                                <div style={{display:'flex', flexWrap:'wrap', gap:8}}>
                                  {[
                                    {label:'Draft SAR', prompt:`Draft Suspicious Activity Report for ${selAcct.account_id}`},
                                    {label:'Explain Risk', prompt:`Explain risk score for account ${selAcct.account_id}`},
                                    {label:'Trace Money Flow', prompt:`Trace money flow for account ${selAcct.account_id}`},
                                    {label:'Community Analysis', prompt:'Explain Louvain community modular partition'},
                                  ].map(({label,prompt})=>(
                                    <button key={label} onClick={()=>{ setView('ai'); sendAI(prompt) }} style={{
                                      padding:'6px 12px', borderRadius:5, background:'var(--bg-surface)',
                                      border:'1px solid var(--border-dim)', cursor:'pointer',
                                      fontSize:11, fontWeight:600, color:'var(--text-secondary)', display:'flex', alignItems:'center', gap:5
                                    }}>
                                      <Sparkles size={11}/> {label}
                                    </button>
                                  ))}
                                </div>
                              </motion.div>
                            )}

                          </AnimatePresence>
                        </div>
                      </>
                    ) : (
                      <div style={{flex:1, display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', gap:12, color:'var(--text-dim)'}}>
                        <Network size={48} opacity={0.3}/>
                        <h3 style={{fontSize:13, fontWeight:700, color:'var(--text-muted)'}}>No Target Selected</h3>
                        <p style={{fontSize:11, color:'var(--text-dim)', textAlign:'center', maxWidth:280}}>Select an account from the Targets list to begin investigation workspace.</p>
                      </div>
                    )}
                  </div>
                </div>
              </motion.div>
            )}

            {/* ═══ AI COPILOT VIEW ═══ */}
            {view==='ai' && (
              <motion.div key="ai" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
                style={{flex:1, overflow:'hidden', display:'flex', flexDirection:'column', padding:20, gap:16}}>

                <div style={{display:'flex', alignItems:'center', gap:10}}>
                  <div style={{width:36, height:36, borderRadius:8, background:'rgba(59,130,246,0.12)', border:'1px solid rgba(59,130,246,0.25)', display:'flex', alignItems:'center', justifyContent:'center'}}>
                    <MessageSquare size={16} color="var(--action)"/>
                  </div>
                  <div>
                    <h1 style={{fontSize:16, fontWeight:800, letterSpacing:'-0.01em'}}>CIDECODE AI Copilot</h1>
                    <p style={{fontSize:10, color:'var(--text-muted)', marginTop:1}}>AML investigation assistant — ask about money trails, communities, SAR drafts, risk scores</p>
                  </div>
                </div>

                <div style={{flex:1, overflow:'hidden', display:'grid', gridTemplateColumns:'240px 1fr', gap:14}}>

                  {/* Left: case memory + notes */}
                  <div style={{display:'flex', flexDirection:'column', gap:10, overflow:'auto'}}>
                    <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, padding:14}}>
                      <div className="label-xs" style={{marginBottom:10}}>CASE MEMORY</div>
                      {[
                        {label:'Active Target', value:selAcct?.account_id||'None selected'},
                        {label:'Communities Detected', value:'13 communities · Ring #7'},
                        {label:'DB Phase', value:'Phase 8 analytics loaded'},
                        {label:'Model State', value:'Calibrated (IF+XGB+Meta)'},
                      ].map(({label,value})=>(
                        <div key={label} style={{marginBottom:8, paddingBottom:8, borderBottom:'1px solid var(--border-dim)'}}>
                          <div className="label-xs" style={{marginBottom:2}}>{label}</div>
                          <div style={{fontSize:11, color:'var(--text-primary)', fontFamily:'var(--font-mono)'}}>{value}</div>
                        </div>
                      ))}
                    </div>

                    {/* Notes */}
                    <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, padding:14, flex:1, display:'flex', flexDirection:'column', gap:8}}>
                      <div style={{display:'flex', alignItems:'center', gap:6}}>
                        <BookOpen size={11} color="var(--text-muted)"/>
                        <div className="label-xs">INVESTIGATION NOTES</div>
                      </div>
                      <textarea value={notes} onChange={e=>setNotes(e.target.value)}
                        placeholder="Type case notes, observations, evidence…"
                        style={{
                          flex:1, minHeight:140, resize:'none', background:'var(--bg-base)',
                          border:'1px solid var(--border-dim)', borderRadius:5,
                          color:'var(--text-secondary)', fontSize:11, padding:10, outline:'none',
                          fontFamily:'var(--font-mono)', lineHeight:1.6
                        }}/>
                      {notes && (
                        <button onClick={()=>{ const blob=new Blob([notes],{type:'text/plain'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=`investigation_notes_${Date.now()}.txt`; a.click() }}
                          style={{padding:'5px 10px', borderRadius:5, background:'var(--action-dim)', border:'1px solid var(--border-blue)', cursor:'pointer', fontSize:10, fontWeight:700, color:'var(--action)', display:'flex', alignItems:'center', gap:5}}>
                          <Download size={11}/> Export Notes
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Right: chat */}
                  <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, display:'flex', flexDirection:'column', overflow:'hidden'}}>

                    {/* Messages */}
                    <div style={{flex:1, overflow:'auto', padding:'16px 16px 8px', display:'flex', flexDirection:'column', gap:12}}>
                      {msgs.map((m,i)=>(
                        <div key={i} style={{display:'flex', justifyContent:m.sender==='user'?'flex-end':'flex-start'}}>
                          <div className={m.sender==='ai'?'chat-assistant':'chat-user'}
                            style={{maxWidth:'85%', padding:'12px 14px', borderRadius:m.sender==='ai'?'0 8px 8px 8px':'8px 0 8px 8px'}}>
                            <div className="label-xs" style={{marginBottom:6, color:m.sender==='ai'?'var(--action)':'var(--text-muted)'}}>
                              {m.sender==='ai'?'CIDECODE AI':'INVESTIGATOR'}
                            </div>
                            <pre style={{margin:0, fontSize:11, lineHeight:1.7, whiteSpace:'pre-wrap', fontFamily:'var(--font-ui)', color:'var(--text-secondary)'}}>{m.text}</pre>
                            {m.sources && m.sources.length>0 && (
                              <div style={{display:'flex', flexWrap:'wrap', gap:5, marginTop:10, paddingTop:8, borderTop:'1px solid var(--border-dim)'}}>
                                <span className="label-xs" style={{alignSelf:'center'}}>Sources:</span>
                                {m.sources.map(s=>(
                                  <span key={s} className="evidence-source">{s}</span>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}

                      {/* Streaming */}
                      {streaming && (
                        <div style={{display:'flex', justifyContent:'flex-start'}}>
                          <div className="chat-assistant" style={{maxWidth:'85%', padding:'12px 14px', borderRadius:'0 8px 8px 8px'}}>
                            <div className="label-xs" style={{marginBottom:6, color:'var(--action)'}}>CIDECODE AI</div>
                            <pre style={{margin:0, fontSize:11, lineHeight:1.7, whiteSpace:'pre-wrap', fontFamily:'var(--font-ui)', color:'var(--text-secondary)'}}>
                              {streamTxt}<span className="copilot-cursor"/>
                            </pre>
                          </div>
                        </div>
                      )}
                      <div ref={chatEnd}/>
                    </div>

                    {/* Quick prompts */}
                    <div style={{padding:'8px 14px', borderTop:'1px solid var(--border-dim)', display:'flex', flexWrap:'wrap', gap:6, flexShrink:0}}>
                      {[
                        {label:'📄 Draft SAR', p:'Draft Suspicious Activity Report (SAR)'},
                        {label:'🔍 Explain Risk', p:'Explain risk drivers for the active target'},
                        {label:'🔗 Community Map', p:'Explain Louvain community modular partition'},
                        {label:'💰 Trace Funds', p:'Trace the money flow and layering patterns'},
                      ].map(({label,p})=>(
                        <button key={label} onClick={()=>sendAI(p)} disabled={streaming} style={{
                          padding:'4px 10px', borderRadius:4, background:'var(--bg-raised)',
                          border:'1px solid var(--border-dim)', cursor:streaming?'not-allowed':'pointer',
                          fontSize:10, fontWeight:600, color:'var(--text-muted)', opacity:streaming?0.5:1,
                          transition:'all 0.15s'
                        }}>{label}</button>
                      ))}
                    </div>

                    {/* Input */}
                    <form onSubmit={onSend} style={{padding:'8px 14px 14px', display:'flex', gap:8, flexShrink:0}}>
                      <input value={aiInput} onChange={e=>setAiInput(e.target.value)} disabled={streaming}
                        placeholder="Request money trail summaries, SAR drafts, anomaly indicators…"
                        style={{
                          flex:1, height:38, padding:'0 12px',
                          background:'var(--bg-base)', border:'1px solid var(--border-mid)', borderRadius:6,
                          color:'var(--text-primary)', fontSize:12, outline:'none', fontFamily:'var(--font-ui)'
                        }}/>
                      <button type="submit" disabled={streaming||!aiInput.trim()} style={{
                        width:38, height:38, borderRadius:6, background:'var(--action)',
                        border:'none', cursor:streaming?'not-allowed':'pointer', display:'flex',
                        alignItems:'center', justifyContent:'center', opacity:streaming?0.5:1
                      }}>
                        <Send size={14} color="#fff"/>
                      </button>
                    </form>
                  </div>
                </div>
              </motion.div>
            )}

            {/* ═══ UPLOAD VIEW ═══ */}
            {view==='upload' && (
              <motion.div key="up" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
                style={{flex:1, overflow:'auto', padding:24, maxWidth:700}}>
                <div style={{marginBottom:20}}>
                  <h1 style={{fontSize:18, fontWeight:800}}>Statement Ingestion Pipeline</h1>
                  <p style={{fontSize:11, color:'var(--text-muted)', marginTop:4}}>Upload PDF, CSV, XLSX, or scanned images. OCR + field extraction + schema mapping run automatically.</p>
                </div>

                <form onSubmit={onUpload} style={{display:'flex', flexDirection:'column', gap:16}}>
                  {/* Dropzone */}
                  <div onDragOver={e=>e.preventDefault()} onDrop={onDrop}
                    style={{
                      border:'2px dashed var(--border-mid)', borderRadius:10,
                      padding:'48px 24px', textAlign:'center', cursor:'pointer',
                      transition:'border-color 0.2s', background:'var(--bg-surface)'
                    }}
                    onMouseEnter={e=>(e.currentTarget.style.borderColor='rgba(59,130,246,0.4)')}
                    onMouseLeave={e=>(e.currentTarget.style.borderColor='var(--border-mid)')}>
                    <UploadCloud size={40} color="var(--action)" style={{marginBottom:14}}/>
                    <h3 style={{fontSize:13, fontWeight:700}}>Drop Bank Statements Here</h3>
                    <p style={{fontSize:11, color:'var(--text-muted)', marginTop:6}}>PDF · CSV · XLSX · XLS · PNG · JPG (OCR) · JSON</p>
                    <input type="file" multiple id="fup" style={{display:'none'}} onChange={onFilePick}/>
                    <label htmlFor="fup" style={{
                      display:'inline-block', marginTop:16, padding:'7px 16px', borderRadius:5,
                      background:'var(--bg-raised)', border:'1px solid var(--border-mid)',
                      cursor:'pointer', fontSize:11, fontWeight:700, color:'var(--text-secondary)'
                    }}>Select Files</label>
                  </div>

                  {/* File queue */}
                  {upFiles.length>0 && (
                    <div style={{background:'var(--bg-surface)', border:'1px solid var(--border-dim)', borderRadius:8, overflow:'hidden'}}>
                      <div style={{padding:'10px 14px', borderBottom:'1px solid var(--border-dim)', display:'flex', justifyContent:'space-between', alignItems:'center'}}>
                        <div className="label-xs">INGESTION QUEUE — {upFiles.length} file{upFiles.length!==1?'s':''}</div>
                        <button type="button" onClick={()=>setUpFiles([])} style={{background:'none', border:'none', cursor:'pointer', color:'var(--text-muted)'}}>
                          <X size={14}/>
                        </button>
                      </div>
                      {upFiles.map((f,i)=>(
                        <div key={i} style={{padding:'8px 14px', display:'flex', justifyContent:'space-between', alignItems:'center', borderBottom:'1px solid var(--border-dim)'}}>
                          <span style={{fontSize:11, fontFamily:'var(--font-mono)', color:'var(--text-secondary)'}}>{f.name}</span>
                          <span style={{fontSize:10, color:'var(--text-dim)', fontFamily:'var(--font-mono)'}}>{(f.size/1024).toFixed(1)} KB</span>
                        </div>
                      ))}
                      <div style={{padding:'10px 14px', display:'flex', justifyContent:'flex-end', gap:8}}>
                        <button type="button" onClick={()=>setUpFiles([])} style={{padding:'6px 14px', borderRadius:5, background:'var(--bg-raised)', border:'1px solid var(--border-dim)', cursor:'pointer', fontSize:11, fontWeight:700, color:'var(--text-muted)'}}>Clear</button>
                        <button type="submit" disabled={upStatus==='uploading'} style={{padding:'6px 14px', borderRadius:5, background:'var(--action)', border:'none', cursor:'pointer', fontSize:11, fontWeight:700, color:'#fff', opacity:upStatus==='uploading'?0.6:1}}>
                          {upStatus==='uploading'?'Extracting…':'Run Extraction Pipeline'}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Result */}
                  {upStatus==='done' && upResults && (
                    <div style={{padding:16, borderRadius:8, background:'rgba(16,185,129,0.06)', border:'1px solid rgba(16,185,129,0.2)'}}>
                      <div style={{display:'flex', alignItems:'center', gap:8, fontSize:12, fontWeight:700, color:'#10b981', marginBottom:10}}>
                        <CheckCircle2 size={15}/> Ingestion completed
                      </div>
                      <div style={{display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:10}}>
                        {[['Rows Parsed', upResults.rows_parsed],['OCR Confidence','97.4%'],['Banks Detected', upResults.banks_detected||'—']].map(([l,v])=>(
                          <div key={l} style={{padding:'8px 10px', borderRadius:6, background:'var(--bg-surface)', border:'1px solid var(--border-dim)'}}>
                            <div className="label-xs" style={{marginBottom:4}}>{l}</div>
                            <div style={{fontSize:14, fontWeight:800, fontFamily:'var(--font-mono)', color:'var(--text-primary)'}}>{v}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {upStatus==='error' && (
                    <div style={{padding:'10px 14px', borderRadius:6, background:'rgba(239,68,68,0.06)', border:'1px solid rgba(239,68,68,0.2)', display:'flex', alignItems:'center', gap:8, fontSize:11, color:'var(--threat-critical)'}}>
                      <AlertTriangle size={14}/> Pipeline error. Check backend console for details.
                    </div>
                  )}
                </form>
              </motion.div>
            )}

          </AnimatePresence>
        </main>
      </div>

      {/* ═══ COMMAND PALETTE ═══ */}
      <AnimatePresence>
        {cmd && (
          <motion.div key="cmd" initial={{opacity:0}} animate={{opacity:1}} exit={{opacity:0}}
            className="cmd-backdrop"
            onClick={()=>setCmd(false)}
            style={{position:'fixed', inset:0, zIndex:100, display:'flex', justifyContent:'center', alignItems:'flex-start', paddingTop:100}}>
            <motion.div initial={{scale:0.95, y:-14}} animate={{scale:1, y:0}} exit={{scale:0.95, y:-14}}
              onClick={e=>e.stopPropagation()}
              style={{width:540, background:'rgba(5,8,18,0.98)', border:'1px solid var(--border-lit)', borderRadius:10, overflow:'hidden', boxShadow:'0 24px 60px rgba(0,0,0,0.7)'}}>
              <div style={{position:'relative', borderBottom:'1px solid var(--border-dim)'}}>
                <SearchIcon size={16} style={{position:'absolute', left:16, top:'50%', transform:'translateY(-50%)', color:'var(--text-muted)'}}/>
                <input autoFocus value={cmdQ} onChange={e=>setCmdQ(e.target.value)}
                  placeholder="Account ID, bank name, or navigation command…"
                  style={{width:'100%', padding:'14px 14px 14px 44px', background:'transparent', border:'none', color:'var(--text-primary)', fontSize:13, outline:'none', fontFamily:'var(--font-ui)'}}/>
                <kbd onClick={()=>setCmd(false)} style={{position:'absolute', right:12, top:'50%', transform:'translateY(-50%)', padding:'2px 6px', borderRadius:4, background:'var(--bg-raised)', border:'1px solid var(--border-mid)', fontSize:10, color:'var(--text-dim)', cursor:'pointer', fontFamily:'var(--font-mono)'}}>ESC</kbd>
              </div>
              <div style={{maxHeight:340, overflow:'auto', padding:8}}>
                {cmdQ ? (
                  <>
                    <div className="label-xs" style={{padding:'6px 10px 4px'}}>MATCHING ACCOUNTS</div>
                    {cmdAccts.length>0 ? cmdAccts.map(a=>(
                      <div key={a.account_id}
                        onClick={()=>{ setSelAcct(a); setView('intel'); setWsTab('overview'); setCmd(false); setCmdQ('') }}
                        style={{display:'flex', justifyContent:'space-between', alignItems:'center', padding:'8px 10px', borderRadius:5, cursor:'pointer', transition:'background 0.1s'}}
                        onMouseEnter={e=>(e.currentTarget.style.background='var(--bg-raised)')}
                        onMouseLeave={e=>(e.currentTarget.style.background='transparent')}>
                        <span style={{fontSize:12, fontWeight:700, fontFamily:'var(--font-mono)', color:'var(--text-primary)'}}>{a.account_id}</span>
                        <div style={{display:'flex', gap:8, alignItems:'center'}}>
                          <span style={{fontSize:10, color:'var(--text-muted)'}}>{a.bank_name}</span>
                          <span className={`flag-chip badge-${riskTier(a.risk_score).label.toLowerCase()}`} style={{fontSize:9}}>{a.risk_score.toFixed(0)}</span>
                        </div>
                      </div>
                    )) : <div style={{padding:'10px 10px', fontSize:11, color:'var(--text-dim)'}}>No accounts match "{cmdQ}"</div>}
                  </>
                ) : (
                  <>
                    <div className="label-xs" style={{padding:'6px 10px 4px'}}>QUICK NAVIGATION</div>
                    {[
                      {label:'Intelligence Overview', view:'dash' as const, hint:'G+D'},
                      {label:'Investigation Center', view:'intel' as const, hint:'G+I'},
                      {label:'AI Copilot', view:'ai' as const, hint:'G+A'},
                      {label:'Statement Ingestion', view:'upload' as const, hint:'G+U'},
                    ].map(({label,view:v,hint})=>(
                      <div key={v}
                        onClick={()=>{ setView(v); setCmd(false) }}
                        style={{display:'flex', justifyContent:'space-between', alignItems:'center', padding:'8px 10px', borderRadius:5, cursor:'pointer'}}
                        onMouseEnter={e=>(e.currentTarget.style.background='var(--bg-raised)')}
                        onMouseLeave={e=>(e.currentTarget.style.background='transparent')}>
                        <span style={{fontSize:12, fontWeight:600, color:'var(--text-secondary)'}}>{label}</span>
                        <kbd style={{padding:'2px 6px', borderRadius:3, background:'var(--bg-raised)', border:'1px solid var(--border-dim)', fontSize:9, color:'var(--text-dim)', fontFamily:'var(--font-mono)'}}>{hint}</kbd>
                      </div>
                    ))}
                  </>
                )}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ═══ ONBOARDING TOAST ═══ */}
      <AnimatePresence>
        {onboarding && (
          <motion.div initial={{opacity:0, y:24}} animate={{opacity:1, y:0}} exit={{opacity:0, y:24}}
            style={{position:'fixed', bottom:20, right:20, zIndex:90, maxWidth:320,
              background:'rgba(5,8,18,0.97)', border:'1px solid var(--border-blue)', borderRadius:10,
              padding:'14px 16px', boxShadow:'0 12px 40px rgba(0,0,0,0.6)'
            }}>
            <div style={{display:'flex', alignItems:'center', gap:8, marginBottom:8}}>
              <Radio size={13} color="var(--action)"/>
              <span style={{fontSize:12, fontWeight:700}}>CIDECODE Workspace Ready</span>
            </div>
            <p style={{fontSize:11, color:'var(--text-muted)', lineHeight:1.6}}>
              CID Karnataka AML Intelligence Platform online. Press <kbd style={{padding:'1px 5px', borderRadius:3, background:'var(--bg-raised)', border:'1px solid var(--border-dim)', fontFamily:'var(--font-mono)', fontSize:10}}>⌘K</kbd> to search accounts, or select a suspect from the queue.
            </p>
            <button onClick={()=>setOnboarding(false)} style={{
              marginTop:12, padding:'5px 12px', borderRadius:5, background:'var(--action-dim)',
              border:'1px solid var(--border-blue)', cursor:'pointer', fontSize:10, fontWeight:700, color:'var(--action)'
            }}>Acknowledge</button>
          </motion.div>
        )}
      </AnimatePresence>

    </div>
  )
}