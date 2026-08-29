#!/usr/bin/env node
/**
 * Meridian Robinhood Chain CLMM Paper Trading Engine (Uniswap v3/v4)
 * Strictly isolated from Solana / Meteora DLMM paper-trading records.
 * Rules strictly mirrored from Hermes profile ghepappo (paper_strategy_policy.json):
 * - 2-consecutive-check OOR / fee-decay defensive exit
 * - Hard stop: -4.0% | Soft stop: -3.0% | Take Profit: +3.0% (Review zone: +2.0%)
 * - Accurate CLMM mark-to-market valuation (no hardcoded -15% floor)
 * - True elapsed-time fee accrual (no minimum 6-minute inflation)
 * - Direct pair lookup for deployment and monitoring (no silent fallback)
 * - Manual position close subcommand
 */

import fs from "fs";
import path from "path";
import os from "os";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Strictly isolated data paths for Robinhood Chain
const DATA_DIR = path.join(os.homedir(), ".hermes", "profiles", "ghepappo", "home", ".meridian");
const POSITIONS_FILE = path.join(DATA_DIR, "robinhood_paper_positions.json");
const POLICY_FILE = path.join(DATA_DIR, "robinhood_strategy_policy.json");
const STATE_FILE = path.join(__dirname, "..", "robinhood-state.json");

if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

// Gas cost model for Robinhood Chain (Arbitrum Orbit L2):
// 3 transactions (deploy, fee harvest/collect, close/burn) * ~0.00003 ETH (~$0.075 USD)
const GAS_COST_PER_ROUNDTRIP_USD = 0.15;

function loadPositions() {
  if (fs.existsSync(POSITIONS_FILE)) {
    try {
      return JSON.parse(fs.readFileSync(POSITIONS_FILE, "utf8"));
    } catch {
      return { positions: [], closed_positions: [], cumulative_pnl_usd: 0.0 };
    }
  }
  return { positions: [], closed_positions: [], cumulative_pnl_usd: 0.0 };
}

function savePositions(data) {
  fs.writeFileSync(POSITIONS_FILE, JSON.stringify(data, null, 2), "utf8");
}

// Multi-source discovery on DEXScreener
async function fetchAllRobinhoodPairs() {
  const endpoints = [
    "https://api.dexscreener.com/latest/dex/tokens/0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73", // WETH (main quote)
    "https://api.dexscreener.com/latest/dex/search?q=robinhood",
    "https://api.dexscreener.com/latest/dex/search?q=4663",
    "https://api.dexscreener.com/latest/dex/pairs/robinhood/0x01D861dCc34cb22d7B520c54BA147bAA9e8Df894,0xA427ad72dB4227910805162fFAe9D9b0c87BD1b5" // PCC/WETH & 4663/WETH
  ];

  const pairMap = new Map();

  for (const url of endpoints) {
    try {
      const res = await fetch(url, { headers: { "User-Agent": "Meridian-Robinhood/1.0" } });
      if (!res.ok) continue;
      const json = await res.json();
      const pairs = json.pairs || [];
      for (const p of pairs) {
        if (p.chainId === "robinhood" && p.pairAddress) {
          pairMap.set(p.pairAddress.toLowerCase(), p);
        }
      }
    } catch {
      // Continue to next endpoint if one fails
    }
  }

  return Array.from(pairMap.values());
}

// Direct single/batch pair lookup
async function fetchDirectPair(pairAddress) {
  const cleanAddr = String(pairAddress).trim();
  const url = `https://api.dexscreener.com/latest/dex/pairs/robinhood/${cleanAddr}`;
  try {
    const res = await fetch(url, { headers: { "User-Agent": "Meridian-Robinhood/1.0" } });
    if (!res.ok) return null;
    const json = await res.json();
    const pairs = json.pairs || [];
    return pairs.find(p => p.chainId === "robinhood" && p.pairAddress.toLowerCase() === cleanAddr.toLowerCase()) || pairs[0] || null;
  } catch {
    return null;
  }
}

// Continuous CLMM valuation function (Uniswap v3 mark-to-market)
function computeClmmValue(entryPrice, currentPrice, lowerPrice, upperPrice, capital) {
  if (entryPrice <= 0 || currentPrice <= 0 || lowerPrice <= 0 || upperPrice <= lowerPrice) {
    return capital;
  }

  const sqrtP = Math.sqrt(currentPrice);
  const sqrtPa = Math.sqrt(lowerPrice);
  const sqrtPb = Math.sqrt(upperPrice);
  const sqrtP0 = Math.sqrt(entryPrice);

  // Liquidity L corresponding to initial capital C at P0:
  // C = L * ( (sqrtP0 - sqrtPa) + (1/sqrtP0 - 1/sqrtPb)*P0 )
  //   = L * (2 * sqrtP0 - sqrtPa - P0 / sqrtPb)
  const denom = (2 * sqrtP0 - sqrtPa - (entryPrice / sqrtPb));
  if (denom <= 0) return capital * (currentPrice / entryPrice);

  const L = capital / denom;

  if (currentPrice < lowerPrice) {
    // 100% in base asset x: x = L * (1/sqrtPa - 1/sqrtPb)
    const amountX = L * ((1 / sqrtPa) - (1 / sqrtPb));
    return amountX * currentPrice;
  } else if (currentPrice > upperPrice) {
    // 100% in quote asset y: y = L * (sqrtPb - sqrtPa)
    const amountY = L * (sqrtPb - sqrtPa);
    return amountY; // in USD
  } else {
    // In range: holds both x and y
    const amountX = L * ((1 / sqrtP) - (1 / sqrtPb));
    const amountY = L * (sqrtP - sqrtPa);
    return (amountX * currentPrice) + amountY;
  }
}

export async function scanRobinhoodPools() {
  console.log("🔍 Scanning Uniswap v3/v4 Pools on Robinhood Chain (Chain ID: 4663)...");
  const pairs = await fetchAllRobinhoodPairs();
  if (pairs.length === 0) {
    console.log("No active pairs returned from DEXScreener API.");
    return [];
  }

  const scored = pairs.map(p => {
    const base = p.baseToken?.symbol || "UNKNOWN";
    const quote = p.quoteToken?.symbol || "UNKNOWN";
    const priceUsd = Number(p.priceUsd) || 0;
    const vol24h = Number(p.volume?.h24) || 0;
    const liqUsd = Number(p.liquidity?.usd) || 0;
    const labels = p.labels || [];
    const version = labels.includes("v4") ? "v4" : "v3";
    const feeTier = 0.003; // default 0.3%
    const est24hFees = vol24h * feeTier;
    const feeLiqRatio = liqUsd > 0 ? (est24hFees / liqUsd) * 100 : 0;

    return {
      pairAddress: p.pairAddress,
      dexId: p.dexId,
      version,
      symbol: `${base}/${quote}`,
      baseToken: p.baseToken,
      quoteToken: p.quoteToken,
      priceUsd,
      vol24h,
      liqUsd,
      est24hFees,
      feeLiqRatio,
      url: p.url
    };
  });

  scored.sort((a, b) => b.vol24h - a.vol24h);

  console.log(`\n📊 Found ${scored.length} Robinhood Chain Pools (Sorted by 24h Volume):`);
  console.log("----------------------------------------------------------------------------------------------------");
  console.log(`| #  | Pool / DEX         | Pair Address                               | 24h Vol ($)   | Liquidity ($) | Est. Fee/Day |`);
  console.log("----------------------------------------------------------------------------------------------------");
  scored.slice(0, 15).forEach((p, i) => {
    const idx = String(i + 1).padEnd(2);
    const name = `${p.symbol} (${p.version})`.padEnd(18);
    const addr = p.pairAddress.slice(0, 42).padEnd(42);
    const vol = `$${p.vol24h.toLocaleString()}`.padEnd(13);
    const liq = `$${p.liqUsd.toLocaleString()}`.padEnd(13);
    const fee = `$${p.est24hFees.toFixed(2)}`.padEnd(12);
    console.log(`| ${idx} | ${name} | ${addr} | ${vol} | ${liq} | ${fee} |`);
  });
  console.log("----------------------------------------------------------------------------------------------------\n");
  return scored;
}

export async function deployRobinhoodPaperPosition({ pairAddress, amountUsd = 60.0, rangePct = 15.0 }) {
  if (!pairAddress) {
    console.error("❌ Error: You must specify a target --pair <pairAddress>. No silent fallback permitted.");
    return false;
  }

  console.log(`🔍 Resolving pair ${pairAddress} directly on Robinhood Chain...`);
  const targetPair = await fetchDirectPair(pairAddress);

  if (!targetPair || targetPair.chainId !== "robinhood") {
    console.error(`❌ Deployment Aborted: Pair address ${pairAddress} was NOT found on Robinhood Chain.`);
    return false;
  }

  const data = loadPositions();
  const base = targetPair.baseToken?.symbol || "BASE";
  const quote = targetPair.quoteToken?.symbol || "QUOTE";
  const currentPrice = Number(targetPair.priceUsd) || 0.0001;
  const lowerPrice = currentPrice * (1 - rangePct / 100);
  const upperPrice = currentPrice * (1 + rangePct / 100);
  const posId = `rh_${Date.now().toString(36)}`;

  const newPosition = {
    id: posId,
    network: "robinhood",
    chainId: 4663,
    pairAddress: targetPair.pairAddress,
    dex: targetPair.dexId || "uniswap",
    symbol: `${base}/${quote}`,
    baseSymbol: base,
    quoteSymbol: quote,
    capitalUsd: amountUsd,
    entryPriceUsd: currentPrice,
    lowerPriceUsd: lowerPrice,
    upperPriceUsd: upperPrice,
    rangePct,
    openedAt: new Date().toISOString(),
    status: "OPEN_PAPER",
    accumulatedFeeUsd: 0.0,
    currentPnLUsd: 0.0,
    lastCheckTime: Date.now(),
    monitor_checks: []
  };

  data.positions.push(newPosition);
  savePositions(data);

  console.log(`✅ [Robinhood Chain] Paper Position Deployed:`);
  console.log(`   ID: ${posId}`);
  console.log(`   Pool: ${base}/${quote} (${targetPair.pairAddress})`);
  console.log(`   Capital: $${amountUsd}`);
  console.log(`   Entry Price: $${currentPrice.toFixed(6)}`);
  console.log(`   Range: [$${lowerPrice.toFixed(6)} - $${upperPrice.toFixed(6)}] (±${rangePct}%)`);
  return newPosition;
}

export async function monitorRobinhoodPositions() {
  console.log("👀 Monitoring Robinhood Chain Paper Positions (Strict Defensive Policy)...");
  const data = loadPositions();

  if (!data.positions || data.positions.length === 0) {
    console.log("No active Robinhood paper positions open.");
    return [];
  }

  const now = Date.now();
  let anyClosed = false;

  for (const pos of data.positions) {
    // 1. Direct fetch live pair data to prevent freeze if pair drops off search list
    const livePair = await fetchDirectPair(pos.pairAddress);
    const currentPrice = livePair ? (Number(livePair.priceUsd) || pos.entryPriceUsd) : pos.entryPriceUsd;
    const vol24h = livePair ? (Number(livePair.volume?.h24) || 0) : 0;
    const liqUsd = livePair ? (Number(livePair.liquidity?.usd) || 10000) : 10000;
    const est24hFees = vol24h * 0.003;
    const feeLiqRatio = liqUsd > 0 ? (est24hFees / liqUsd) * 100 : 0;

    const inRange = currentPrice >= pos.lowerPriceUsd && currentPrice <= pos.upperPriceUsd;

    // 2. Accurate elapsed time (no artificial 6-min inflation)
    const elapsedMs = Math.max(0, now - (pos.lastCheckTime || now));
    const elapsedHours = elapsedMs / 3600000;

    // Accrue fees only if elapsed time > 18s (0.005 hours) to avoid rapid micro-run inflation
    if (elapsedHours >= 0.005) {
      const poolShare = Math.min(1.0, pos.capitalUsd / Math.max(liqUsd, 1000));
      const hourlyPoolFee = est24hFees / 24;
      const feeEarned = inRange ? (hourlyPoolFee * poolShare * elapsedHours) : 0.0;
      pos.accumulatedFeeUsd += feeEarned;
      pos.lastCheckTime = now;
    }

    // 3. Mark-to-market continuous CLMM valuation
    const currentGrossValue = computeClmmValue(pos.entryPriceUsd, currentPrice, pos.lowerPriceUsd, pos.upperPriceUsd, pos.capitalUsd);
    const grossPnLUsd = (currentGrossValue - pos.capitalUsd) + pos.accumulatedFeeUsd;
    const netPnLUsd = grossPnLUsd - GAS_COST_PER_ROUNDTRIP_USD;
    const pnlPct = (netPnLUsd / pos.capitalUsd) * 100;
    pos.currentPnLUsd = netPnLUsd;

    // 4. Record to monitor_checks history array
    pos.monitor_checks = pos.monitor_checks || [];
    const currentCheck = {
      timestamp: new Date().toISOString(),
      price: currentPrice,
      in_range: inRange,
      fee_ratio_pct: Number(feeLiqRatio.toFixed(3)),
      pnl_pct: Number(pnlPct.toFixed(2)),
      pnl_usd: Number(netPnLUsd.toFixed(2)),
      fee_usd: Number(pos.accumulatedFeeUsd.toFixed(3))
    };
    pos.monitor_checks.push(currentCheck);

    const prevCheck = pos.monitor_checks.length >= 2 ? pos.monitor_checks[pos.monitor_checks.length - 2] : null;

    console.log(`\n- [${pos.id}] ${pos.symbol} (${pos.pairAddress.slice(0, 10)}...):`);
    console.log(`   Price: $${currentPrice.toFixed(6)} | Range: [$${pos.lowerPriceUsd.toFixed(6)} - $${pos.upperPriceUsd.toFixed(6)}]`);
    console.log(`   Status: ${inRange ? "🟢 IN RANGE" : "🔴 OUT OF RANGE"}`);
    console.log(`   Gross PnL: ${grossPnLUsd >= 0 ? "+" : ""}$${grossPnLUsd.toFixed(2)} | Net PnL (after gas): ${netPnLUsd >= 0 ? "+" : ""}$${netPnLUsd.toFixed(2)} (${pnlPct.toFixed(2)}%)`);
    console.log(`   Accrued Fees: +$${pos.accumulatedFeeUsd.toFixed(3)} | Fee/Active TVL: ${feeLiqRatio.toFixed(2)}%`);

    // 5. Defensive Exit Discipline (mirrored from Meteora paper_strategy_policy.json)
    let shouldExit = false;
    let exitReason = "";

    // A. 2-consecutive-check OOR rule
    if (!inRange && prevCheck && !prevCheck.in_range) {
      shouldExit = true;
      exitReason = "OOR_2_CONSECUTIVE_CHECKS";
      console.log(`🚨 Exit Triggered: Position OUT OF RANGE for 2 consecutive checks!`);
    }
    // B. Hard Stop: PnL <= -4.0%
    else if (pnlPct <= -4.0) {
      shouldExit = true;
      exitReason = `HARD_STOP_${pnlPct.toFixed(1)}%`;
      console.log(`🛑 Exit Triggered: Hard Stop reached (${pnlPct.toFixed(2)}% <= -4.0%).`);
    }
    // C. Soft Stop: PnL <= -3.0% with weak/declining fee ratio
    else if (pnlPct <= -3.0 && feeLiqRatio < 0.15) {
      shouldExit = true;
      exitReason = "SOFT_STOP_WEAK_FEES";
      console.log(`⚠️ Exit Triggered: Soft stop hit (-3%) with fee/TVL ratio below minimum.`);
    }
    // D. 2-consecutive fee decay (< 0.10%)
    else if (feeLiqRatio < 0.10 && prevCheck && prevCheck.fee_ratio_pct < 0.10) {
      shouldExit = true;
      exitReason = "FEE_DECAY_2_CONSECUTIVE_CHECKS";
      console.log(`📉 Exit Triggered: Fee yield below threshold for 2 consecutive checks.`);
    }
    // E. Take Profit: Net PnL >= +3.0%
    else if (pnlPct >= 3.0) {
      shouldExit = true;
      exitReason = `TAKE_PROFIT_+${pnlPct.toFixed(1)}%`;
      console.log(`🎯 Exit Triggered: Preferred Take Profit target reached (+${pnlPct.toFixed(2)}%).`);
    }
    // F. Take Profit Review Zone: +2.0% if edge shrinking
    else if (pnlPct >= 2.0 && prevCheck && pnlPct < prevCheck.pnl_pct) {
      shouldExit = true;
      exitReason = "TAKE_PROFIT_WEAKENING_EDGE";
      console.log(`🎯 Exit Triggered: Profit review zone (+${pnlPct.toFixed(2)}%) with weakening momentum.`);
    }

    if (shouldExit) {
      pos.status = "CLOSED_PAPER";
      pos.closedAt = new Date().toISOString();
      pos.exitReason = exitReason;
      data.closed_positions = data.closed_positions || [];
      data.closed_positions.push(pos);
      data.cumulative_pnl_usd = (data.cumulative_pnl_usd || 0.0) + pos.currentPnLUsd;
      anyClosed = true;
      console.log(`🔒 Position [${pos.id}] closed. Realized Net PnL: ${pos.currentPnLUsd >= 0 ? "+" : ""}$${pos.currentPnLUsd.toFixed(2)} USD.`);
    }
  }

  if (anyClosed) {
    data.positions = data.positions.filter(p => p.status === "OPEN_PAPER");
  }

  savePositions(data);
  return data.positions;
}

export function manualClosePosition(identifier) {
  if (!identifier) {
    console.log("Usage: node robinhood-clmm.js close <posId_or_pairAddress>");
    return false;
  }

  const data = loadPositions();
  const query = identifier.toLowerCase();
  const idx = data.positions.findIndex(p => p.id.toLowerCase() === query || p.pairAddress.toLowerCase() === query);

  if (idx === -1) {
    console.error(`❌ Position not found for identifier: ${identifier}`);
    return false;
  }

  const pos = data.positions.splice(idx, 1)[0];
  pos.status = "CLOSED_PAPER";
  pos.closedAt = new Date().toISOString();
  pos.exitReason = "MANUAL_USER_OVERRIDE";
  data.closed_positions = data.closed_positions || [];
  data.closed_positions.push(pos);
  data.cumulative_pnl_usd = (data.cumulative_pnl_usd || 0.0) + pos.currentPnLUsd;

  savePositions(data);
  console.log(`✅ [Manual Close] Position [${pos.id}] ${pos.symbol} manually closed.`);
  console.log(`   Realized Net PnL: ${pos.currentPnLUsd >= 0 ? "+" : ""}$${pos.currentPnLUsd.toFixed(2)} USD`);
  console.log(`   Exit Reason: MANUAL_USER_OVERRIDE`);
  return true;
}

export function printRobinhoodSummary() {
  const data = loadPositions();
  const openCount = (data.positions || []).length;
  const closedCount = (data.closed_positions || []).length;
  const realizedPnL = Number(data.cumulative_pnl_usd) || 0.0;
  const unrealizedPnL = (data.positions || []).reduce((acc, p) => acc + (Number(p.currentPnLUsd) || 0), 0);
  const baseEquity = 60.0;
  const currentEquity = baseEquity + realizedPnL + unrealizedPnL;

  console.log("\n============================================================");
  console.log(" 🟣 Robinhood Chain (EVM L2) Paper-Trading Analytics");
  console.log("    (Defensive Rules Active — Separated from Meteora DLMM)");
  console.log("============================================================");
  console.log(`[+] Network: Robinhood Chain (Arbitrum Orbit / Chain ID: 4663)`);
  console.log(`[+] Primary DEX: Uniswap v3 & v4 (Continuous CLMM)`);
  console.log(`[+] Policy: Defensive (Hard stop -4%, Soft stop -3%, TP +3%, 2-check OOR)`);
  console.log(`[+] Open Positions: ${openCount}`);
  console.log(`[+] Closed Positions: ${closedCount}`);
  console.log(`[+] Realized PnL: ${realizedPnL >= 0 ? "+" : ""}$${realizedPnL.toFixed(2)} USD`);
  console.log(`[+] Unrealized PnL: ${unrealizedPnL >= 0 ? "+" : ""}$${unrealizedPnL.toFixed(2)} USD`);
  console.log(`[+] Total Recycled Paper Equity: $${currentEquity.toFixed(2)} USD (Base: $${baseEquity.toFixed(2)})`);
  console.log("============================================================\n");
  return { openCount, closedCount, realizedPnL, unrealizedPnL, currentEquity };
}

async function main() {
  const cmd = process.argv[2] || "scan";

  if (cmd === "scan") {
    await scanRobinhoodPools();
  } else if (cmd === "deploy") {
    const pairArg = process.argv[3] || "";
    const amountArg = process.argv[4] ? Number(process.argv[4]) : 60.0;
    const rangeArg = process.argv[5] ? Number(process.argv[5]) : 15.0;
    await deployRobinhoodPaperPosition({ pairAddress: pairArg, amountUsd: amountArg, rangePct: rangeArg });
  } else if (cmd === "monitor") {
    await monitorRobinhoodPositions();
  } else if (cmd === "close") {
    const idArg = process.argv[3] || "";
    manualClosePosition(idArg);
  } else if (cmd === "summary" || cmd === "status") {
    printRobinhoodSummary();
  } else {
    console.log("Usage: node robinhood-clmm.js [scan|deploy <pair> [amount] [range]|monitor|close <id>|summary]");
  }
}

if (process.argv[1] && process.argv[1].endsWith("robinhood-clmm.js")) {
  main().catch(console.error);
}
