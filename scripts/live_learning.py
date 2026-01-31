#!/usr/bin/env python3
"""
HOPE Live Learning v2.0
=======================
Automatic model retraining based on trade outcomes.

Features:
- Records trade outcomes
- Triggers retraining every N trades
- Hot-reloads models without restart
- Tracks learning metrics

Run: python live_learning.py --daemon
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
import argparse
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger(__name__)

# Paths
SCRIPT_DIR = Path(__file__).parent
STATE_DIR = SCRIPT_DIR / "state" / "ai"
LEARNING_DIR = STATE_DIR / "learning"
OUTCOMES_DIR = STATE_DIR / "outcomes"

# Ensure directories
LEARNING_DIR.mkdir(parents=True, exist_ok=True)
OUTCOMES_DIR.mkdir(parents=True, exist_ok=True)

class LiveLearner:
    """Continuous learning from trade outcomes"""
    
    def __init__(self, retrain_threshold=100):
        self.retrain_threshold = retrain_threshold
        self.outcomes_file = OUTCOMES_DIR / "history.jsonl"
        self.metrics_file = LEARNING_DIR / "metrics.jsonl"
        self.training_data_file = STATE_DIR / "training_data.jsonl"
        
        # Counters
        self.trades_since_retrain = 0
        self.total_trades = 0
        self.total_wins = 0
        self.total_losses = 0
        
        # Load existing counts
        self._load_state()
    
    def _load_state(self):
        """Load existing state"""
        state_file = LEARNING_DIR / "state.json"
        if state_file.exists():
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    self.trades_since_retrain = state.get('trades_since_retrain', 0)
                    self.total_trades = state.get('total_trades', 0)
                    self.total_wins = state.get('total_wins', 0)
                    self.total_losses = state.get('total_losses', 0)
                    logger.info(f"Loaded state: {self.total_trades} trades, {self.trades_since_retrain} since retrain")
            except:
                pass
    
    def _save_state(self):
        """Save current state"""
        state_file = LEARNING_DIR / "state.json"
        state = {
            'trades_since_retrain': self.trades_since_retrain,
            'total_trades': self.total_trades,
            'total_wins': self.total_wins,
            'total_losses': self.total_losses,
            'last_updated': datetime.now().isoformat()
        }
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    async def record_outcome(self, trade_result: dict):
        """
        Record a trade outcome for learning.
        
        trade_result = {
            'symbol': 'BTCUSDT',
            'entry_price': 84000,
            'exit_price': 84500,
            'pnl_pct': 0.6,
            'outcome': 'TP_HIT',  # or 'SL_HIT', 'TIMEOUT'
            'features': {...},
            'ai_confidence': 0.72,
            'timestamp': '2026-01-31T...'
        }
        """
        # Validate
        if 'outcome' not in trade_result:
            logger.warning("Missing outcome in trade result")
            return
        
        # Add timestamp if missing
        if 'timestamp' not in trade_result:
            trade_result['timestamp'] = datetime.now().isoformat()
        
        # Write to outcomes file
        with open(self.outcomes_file, 'a') as f:
            f.write(json.dumps(trade_result) + '\n')
        
        # Update counters
        self.total_trades += 1
        self.trades_since_retrain += 1
        
        if trade_result['outcome'] in ['TP_HIT', 'WIN', 'PROFIT']:
            self.total_wins += 1
        else:
            self.total_losses += 1
        
        # Log
        win_rate = self.total_wins / self.total_trades * 100 if self.total_trades > 0 else 0
        logger.info(f"📊 Trade recorded: {trade_result['symbol']} | "
                   f"{trade_result['outcome']} | "
                   f"WR: {win_rate:.1f}% | "
                   f"Trades: {self.trades_since_retrain}/{self.retrain_threshold}")
        
        # Save state
        self._save_state()
        
        # Check if retraining needed
        if self.trades_since_retrain >= self.retrain_threshold:
            await self.trigger_retrain()
        
        # Record metrics
        await self._record_metrics(trade_result)
    
    async def _record_metrics(self, trade_result):
        """Record learning metrics"""
        metric = {
            'timestamp': datetime.now().isoformat(),
            'total_trades': self.total_trades,
            'win_rate': self.total_wins / self.total_trades if self.total_trades > 0 else 0,
            'trades_since_retrain': self.trades_since_retrain,
            'last_outcome': trade_result['outcome'],
            'last_symbol': trade_result.get('symbol', 'UNKNOWN'),
            'last_pnl': trade_result.get('pnl_pct', 0)
        }
        
        with open(self.metrics_file, 'a') as f:
            f.write(json.dumps(metric) + '\n')
    
    async def trigger_retrain(self):
        """Trigger model retraining"""
        logger.info("="*50)
        logger.info("🔄 TRIGGERING MODEL RETRAIN")
        logger.info("="*50)
        
        try:
            # 1. Merge outcomes into training data
            await self._merge_outcomes_to_training()
            
            # 2. Run trainer
            import subprocess
            result = subprocess.run(
                [sys.executable, str(SCRIPT_DIR / "hope_ai_trainer.py"), "--all"],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                logger.info("✅ Retraining completed successfully")
                
                # Reset counter
                self.trades_since_retrain = 0
                self._save_state()
                
                # Log retraining event
                retrain_log = {
                    'timestamp': datetime.now().isoformat(),
                    'event': 'RETRAIN_COMPLETE',
                    'total_trades': self.total_trades,
                    'win_rate': self.total_wins / self.total_trades if self.total_trades > 0 else 0
                }
                
                retrain_history = LEARNING_DIR / "retrain_history.jsonl"
                with open(retrain_history, 'a') as f:
                    f.write(json.dumps(retrain_log) + '\n')
            else:
                logger.error(f"❌ Retraining failed: {result.stderr}")
        
        except Exception as e:
            logger.error(f"❌ Retraining error: {e}")
    
    async def _merge_outcomes_to_training(self):
        """Merge recent outcomes into training data"""
        if not self.outcomes_file.exists():
            return
        
        # Read outcomes
        outcomes = []
        with open(self.outcomes_file, 'r') as f:
            for line in f:
                try:
                    outcomes.append(json.loads(line.strip()))
                except:
                    continue
        
        # Append to training data
        with open(self.training_data_file, 'a') as f:
            for outcome in outcomes:
                if 'features' in outcome and 'outcome' in outcome:
                    f.write(json.dumps(outcome) + '\n')
        
        logger.info(f"📝 Merged {len(outcomes)} outcomes to training data")
        
        # Archive outcomes
        archive_file = OUTCOMES_DIR / f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        os.rename(self.outcomes_file, archive_file)
        logger.info(f"📦 Archived outcomes to {archive_file}")
    
    async def daemon_loop(self, check_interval=60):
        """Main daemon loop"""
        logger.info("🚀 Live Learning daemon started")
        logger.info(f"   Retrain threshold: {self.retrain_threshold} trades")
        logger.info(f"   Check interval: {check_interval}s")
        logger.info(f"   Current trades since retrain: {self.trades_since_retrain}")
        
        while True:
            try:
                # Check for new outcomes from external sources
                await self._check_external_outcomes()
                
                # Sleep
                await asyncio.sleep(check_interval)
            
            except asyncio.CancelledError:
                logger.info("Daemon stopped")
                break
            except Exception as e:
                logger.error(f"Daemon error: {e}")
                await asyncio.sleep(10)
    
    async def _check_external_outcomes(self):
        """Check for trade outcomes from other sources"""
        # Check autotrader state for completed trades
        autotrader_state = STATE_DIR / "autotrader" / "completed_trades.jsonl"
        
        if autotrader_state.exists():
            try:
                with open(autotrader_state, 'r') as f:
                    for line in f:
                        trade = json.loads(line.strip())
                        if not trade.get('_recorded'):
                            await self.record_outcome(trade)
                            trade['_recorded'] = True
            except Exception as e:
                pass
    
    def get_status(self):
        """Get current learning status"""
        return {
            'total_trades': self.total_trades,
            'trades_since_retrain': self.trades_since_retrain,
            'retrain_threshold': self.retrain_threshold,
            'win_rate': self.total_wins / self.total_trades if self.total_trades > 0 else 0,
            'wins': self.total_wins,
            'losses': self.total_losses,
            'next_retrain_in': self.retrain_threshold - self.trades_since_retrain
        }

# HTTP API for recording outcomes
async def start_api_server(learner: LiveLearner, port=8300):
    """Start HTTP API for receiving trade outcomes"""
    from aiohttp import web
    
    async def handle_outcome(request):
        try:
            data = await request.json()
            await learner.record_outcome(data)
            return web.json_response({'status': 'ok'})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    async def handle_status(request):
        return web.json_response(learner.get_status())
    
    async def handle_retrain(request):
        await learner.trigger_retrain()
        return web.json_response({'status': 'retraining'})
    
    app = web.Application()
    app.router.add_post('/outcome', handle_outcome)
    app.router.add_get('/status', handle_status)
    app.router.add_post('/retrain', handle_retrain)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    
    logger.info(f"📡 Live Learning API started on http://127.0.0.1:{port}")
    logger.info(f"   POST /outcome - Record trade outcome")
    logger.info(f"   GET  /status  - Get learning status")
    logger.info(f"   POST /retrain - Trigger retraining")

async def main(daemon=False, api_port=8300, retrain_threshold=100):
    """Main entry point"""
    learner = LiveLearner(retrain_threshold=retrain_threshold)
    
    if daemon:
        # Start API server
        try:
            await start_api_server(learner, api_port)
        except Exception as e:
            logger.warning(f"API server failed (aiohttp not installed?): {e}")
        
        # Run daemon
        await learner.daemon_loop()
    else:
        # Just show status
        status = learner.get_status()
        print("\n" + "="*50)
        print("📊 LIVE LEARNING STATUS")
        print("="*50)
        print(f"Total trades:      {status['total_trades']}")
        print(f"Win rate:          {status['win_rate']:.1%}")
        print(f"Wins / Losses:     {status['wins']} / {status['losses']}")
        print(f"Since retrain:     {status['trades_since_retrain']}")
        print(f"Next retrain in:   {status['next_retrain_in']} trades")
        print("="*50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HOPE Live Learning")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    parser.add_argument("--port", type=int, default=8300, help="API port")
    parser.add_argument("--threshold", type=int, default=100, help="Retrain threshold")
    parser.add_argument("--status", action="store_true", help="Show status only")
    
    args = parser.parse_args()
    
    asyncio.run(main(
        daemon=args.daemon,
        api_port=args.port,
        retrain_threshold=args.threshold
    ))
