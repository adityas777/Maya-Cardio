#!/usr/bin/env bash
# 02_power_cut_sim.md — Instructions for power-cut simulation.
# This is a markdown doc, not a shell script, because the steps require
# interactive VM console access.

cat << 'EOF'
# Power Cut Simulation (Test 05)

## Setup (VM-based, if no physical Pi)

1. Run pipeline in a QEMU/KVM VM with the mock:
   ```
   systemctl start biosignal-mock
   systemctl start pipeline
   ```

2. Note the current session_id and next_index:
   ```
   pipeline status > evidence/05_power_cut/status_before.json
   ```

3. Do NOT run `sync` (that would hide the real buffer loss).

4. Force power-off the VM abruptly:
   ```
   virsh destroy <vmname>
   # OR: echo b > /proc/sysrq-trigger (requires sysrq enabled)
   ```

5. Boot the VM again. Note the reboot time.

6. After reboot, pipeline auto-starts via systemd. Check:
   ```
   journalctl -u pipeline -n 50
   pipeline status > evidence/05_power_cut/status_after.json
   ```

7. Check loss records:
   ```
   pipeline export --session SESSION_ID --from device | \
     grep '"reason"' > evidence/05_power_cut/losses.jsonl
   ```

8. Verify loss is listed with reason "crash_or_power_loss":
   ```
   python3 -c "
   import json
   losses = [json.loads(l) for l in open('evidence/05_power_cut/losses.jsonl')]
   assert any(l['reason'] == 'crash_or_power_loss' for l in losses), 'FAIL: no loss record'
   print('PASS: crash_or_power_loss recorded')
   "
   ```

9. Verify loss ≤ 400 samples per sensor (1 second):
   ```
   python3 -c "
   import json
   losses = [json.loads(l) for l in open('evidence/05_power_cut/losses.jsonl')]
   for l in losses:
       if l['reason'] == 'crash_or_power_loss':
           to = l.get('to_index') or l['from_index']
           size = to - l['from_index'] + 1
           assert size <= 400, f'FAIL: loss {size} > 400 samples'
           print(f'PASS: loss = {size} samples (≤ 400 bound)')
   "
   ```

## Expected result
- Pipeline restarts cleanly.
- Loss of at most 1 second of data (400 samples per sensor) is listed.
- Reason = \"crash_or_power_loss\".
- No silent loss (all missing samples accounted for in loss records).
EOF
