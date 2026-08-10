import re, json

with open('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34/deploy_davis/index.html', 'r') as f:
    html = f.read()

# Find the corrupted KDJ_FALLBACK line
line_start = html.find('var KDJ_FALLBACK')
if line_start < 0:
    print('KDJ_FALLBACK not found')
    exit(1)

# Find the end of this line (next newline)
line_end = html.find('\n', line_start)
corrupted_line = html[line_start:line_end]

# Extract the valid JSON: starts from the SECOND {"sh
# The pattern is: var KDJ_FALLBACK = {"sh60041var KDJ_FALLBACK = {"sh600418":...}};
json_start_marker = 'var KDJ_FALLBACK = {'
# Find the LAST occurrence of this marker before the actual data
last_marker = corrupted_line.rfind(json_start_marker)
if last_marker < 0:
    # Try another approach - find {"sh followed by a digit
    idx = corrupted_line.find('{"sh')
    # Find the second occurrence
    idx2 = corrupted_line.find('{"sh', idx + 1)
    if idx2 >= 0:
        json_str_start = idx2
    else:
        json_str_start = idx
else:
    json_str_start = last_marker + len('var KDJ_FALLBACK = ')

# Extract from json_str_start to end of line
json_part = corrupted_line[json_str_start:]

# Clean up the end - remove trailing comment artifacts
# The end should be }}; but might have extra text
# Find the last }}
last_brace = json_part.rfind('}}')
if last_brace >= 0:
    json_str = json_part[:last_brace + 2]
else:
    # Try to find }; 
    last_semi = json_part.rfind('};')
    json_str = json_part[:last_semi + 1]

# Verify JSON
try:
    kdj_data = json.loads(json_str)
    print(f'Extracted valid JSON: {len(kdj_data)} stocks')
except Exception as e:
    print(f'JSON parse failed: {e}')
    print(f'First 200 chars: {json_str[:200]}')
    print(f'Last 200 chars: {json_str[-200:]}')
    exit(1)

# Build clean replacement line
clean_line = f'var KDJ_FALLBACK = {json.dumps(kdj_data, ensure_ascii=False)};'

# Replace the corrupted line
html = html[:line_start] + clean_line + html[line_end:]

# Write back
with open('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34/deploy_davis/index.html', 'w') as f:
    f.write(html)

print(f'Fixed! KDJ_FALLBACK now has {len(kdj_data)} stocks, clean JSON')
print('Line starts with: var KDJ_FALLBACK = {"sh600418":...')
print('Line ends with: ...}}};')

# Verify by re-reading
with open('/Users/fuckyouasshole/WorkBuddy/2026-07-01-11-18-34/deploy_davis/index.html', 'r') as f:
    html2 = f.read()

# Find and validate KDJ_FALLBACK again
start = html2.find('var KDJ_FALLBACK')
obj_start = html2.find('{', start)
depth = 0
end = obj_start
for i in range(obj_start, min(obj_start + 200000, len(html2))):
    if html2[i] == '{':
        depth += 1
    elif html2[i] == '}':
        depth -= 1
        if depth == 0:
            end = i
            break

try:
    json.loads(html2[obj_start:end+1])
    print('Verification: KDJ_FALLBACK is valid JSON')
except Exception as e:
    print(f'Verification FAILED: {e}')
    print(f'Context: {html2[start:start+100]}')
