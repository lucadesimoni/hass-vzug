# Reliable Sources for Adding Additional Functionality

Based on my analysis of the codebase, here are the reliable sources you can use to add additional functionality and attributes:

## 1. **Test Fixtures (Most Reliable Source)**

**Location:** `tests/fixtures/*/responses/`

The test fixtures contain **real API responses** from actual V-ZUG devices. These are the most reliable source because they represent what the devices actually return.

### What's Available:
- **Device Status Responses**: `ai_get_devicestatus.json` - Shows all available fields in DeviceStatus
- **Program Information**: `hh_get_getprogram.json` - Shows program structure and available options
- **Eco Info**: `hh_get_ecoinfo.json` - Shows water/energy metrics structure
- **Command Details**: `hh_get_command_details.json` - Shows all configurable commands
- **Device Info**: `hh_get_deviceinfo.json` - Shows device metadata structure

### How to Use:
1. Examine response files for fields not currently exposed as entities
2. Compare across different device types to see which features are device-specific
3. Use these as reference when implementing new entity attributes

**Example:** The `DeviceStatus` shows fields like:
- `DeviceName`
- `Serial`
- `Inactive`
- `Program`
- `Status`
- `ProgramEnd` (with `End` and `EndType`)
- `deviceUuid`

## 2. **Existing API Methods (Partially Implemented)**

**Location:** `custom_components/vzug/api/__init__.py`

Several API methods are implemented but not fully utilized:

### `getProgram()` - Lines 700-709
**Current Status:** Implemented but marked with TODO
**What it returns:** Detailed program information including:
- Program ID and name
- Status (selected/available)
- Start time options (min/max/step)
- Duration
- Various boolean options (energySaving, optiStart, steamfinish, partialload, rinsePlus, dryPlus)
- Step IDs

**Potential Use Cases:**
- Create a select entity for program selection
- Expose program options as separate entities
- Show program details in entity attributes

### `setProgram()` - Lines 711-725
**Current Status:** Implemented but not used by any entities
**What it does:** Allows setting program options
**Potential Use Cases:**
- Button/Service to start programs
- Select entity to choose programs
- Number entities for program options (start time, duration)

### `getAllProgramIds()` - Lines 727-734
**Current Status:** Implemented but marked with TODO
**What it returns:** List of all available program IDs for the device
**Potential Use Cases:**
- Program selection entity
- Validation for program-related features

### `getZHMode()` - Lines 663-670
**Current Status:** Implemented but currently returns -1 (commented out in aggregate_state)
**Potential Use Cases:**
- Sensor entity showing ZH mode status
- Switch/select for ZH mode control

## 3. **API Compatibility Table**

**Location:** `CONTRIBUTING.md` (lines 89-110)

This table shows which endpoints are confirmed to work on which devices:

| Endpoint | Adora Dish v6000 | Adora SQL | Adora TSQL WP | Adora Wash v6000 | Combair Steamer v6000 |
|----------|------------------|-----------|---------------|------------------|----------------------|
| `hh?getProgram` | ✅ Yes | ❌ No | ❌ No | ✅ Yes | ✅ Yes |
| `hh?setProgram` | ✅ Yes | ❌ No | ❌ No | ✅ Yes | ✅ Yes |
| `hh?getAllProgramIds` | ✅ Yes | ❌ No | ❌ No | ✅ Yes | ✅ Yes |
| `hh?getZHMode` | ✅ Yes | ❌ No | ❌ No | ✅ Yes | ✅ Yes |

**How to Use:**
- Use this table to determine if a feature should be device-specific
- Check device support before implementing new entities
- Consider conditional entity creation based on device type

## 4. **Program Documentation**

**Location:** `docs/programs.md`

Contains documented program IDs for different device types:
- AdoraDish V6000: Programs 50-61, 87-95
- AdoraWash V6000: Programs 3000-3021
- Combi-Steam XSL: Extensive list of programs
- Combair XSL: Extensive list of programs

**Potential Use Cases:**
- Create program name mappings for select entities
- Validate program IDs
- Document available programs per device

## 5. **Diagnostics Collection**

**Location:** `custom_components/vzug/diagnostics.py`

The `gather_full_api_sample()` function already calls **all available API endpoints**. You can:

1. **Use Home Assistant Diagnostics:**
   - Go to Settings > System > Diagnostics
   - Select a V-ZUG device
   - View complete API responses
   - This shows all available data in real-time

2. **Extend the diagnostics function** to discover new endpoints by examining the responses

## 6. **Device-Specific Expected Data**

**Location:** `tests/fixtures/*/expected.py`

These files show the **expected structure** of responses for each device type. Compare these to see:
- Which fields are consistent across devices
- Which fields are device-specific
- What data types are used

## 7. **Command Configuration Discovery**

**Location:** `custom_components/vzug/config_coord` (via `aggregate_config()`)

The config coordinator already discovers all available commands dynamically. Current implementation creates entities for:
- `type: "boolean"` → Switch entities
- `type: "selection"` → Select entities  
- `type: "range"` → Number entities
- `type: "action"` → Button entities
- `type: "status"` → Sensor entities

**Potential Extensions:**
- Check command `options` for additional attributes
- Use `refresh` field to discover dependent commands
- Explore `alterable` field variations

## 8. **Collection Script**

**Location:** `tests/fixtures/collect_responses.py` and `scripts/collect_responses.sh`

You can use these tools to **capture new API responses** from additional devices:
- Run `collect_responses.sh` with a new device
- Compare responses to find new fields
- Add to test fixtures for validation

## Recommendations for Adding Features

### 1. **Program Selection/Control** (High Value)
**Source:** `getProgram()`, `setProgram()`, `getAllProgramIds()`, `docs/programs.md`

Create:
- Select entity for program selection (using program names from responses)
- Button entities for program options (steamfinish, energySaving, etc.)
- Number entity for program start time
- Service to set full program configuration

### 2. **ZH Mode** (Medium Value)
**Source:** `getZHMode()` API method

Create:
- Sensor showing current ZH mode
- Select/switch entity if ZH mode is alterable

### 3. **Program Details Attributes** (Low Effort, High Value)
**Source:** `getProgram()` response structure

Add to existing entities:
- Extra state attributes with program details
- Expose program options (steamfinish, partialload, etc.) as attributes

### 4. **Additional Device Status Fields**
**Source:** `ai_get_devicestatus.json` test fixtures

Review fields in DeviceStatus that might not be fully exposed:
- All fields appear to be used, but could add more attributes

### 5. **Device-Specific Features**
**Source:** API compatibility table, device-specific test fixtures

Create conditional entities based on device type:
- Some devices support `getProgram`, others don't
- Use `shared.meta.api_version` or `shared.meta.model_id` to conditionally create entities

## How to Validate New Features

1. **Check Test Fixtures**: Ensure the data structure exists in test responses
2. **Test on Real Device**: Use diagnostics to verify responses
3. **Check API Compatibility Table**: Confirm endpoint works on target devices
4. **Add to Diagnostics**: Include in `gather_full_api_sample()` for debugging
5. **Create Test Cases**: Add to test fixtures for new functionality

## Limitations

⚠️ **No Official API Documentation**: There doesn't appear to be official V-ZUG API documentation. The integration is based on reverse-engineered API responses.

✅ **Best Approach**: Use the test fixtures (real device responses) as the source of truth, supplemented by the diagnostics tool for real-time data exploration.

