Steps to Get Correct Primitive Terminal Indices

  1. Find a VI that uses the primitive
  find samples -name "*_BDHb.xml" -exec grep -l "primResID>8055" {} \;
  2. Extract terminal data from XML
  for elem in root.iter():
      prim_res_id = elem.find("primResID")
      if prim_res_id is not None and prim_res_id.text == "8055":
          term_list = elem.find("termList")
          for term in term_list.findall("SL__arrayElement"):
              dco = term.find("dco")
              # Get parmIndex from dco element
              parm_idx_elem = dco.find("parmIndex")
              parm_idx = int(parm_idx_elem.text) if parm_idx_elem else list_position

              # Get direction from bit 0 of combined flags
              flags = term_flags | dco_flags
              is_output = bool(flags & 0x1)  # Bit 0 = isIndicator = output
  3. Map terminals to names
    - Look at the hint variables (e.g., path, error_in)
    - Match input indices to hint input variables
    - Match output indices to hint output variables
  4. Update primitives.json
  "8055": {
    "terminals": [
      {"index": 0, "direction": "out", "name": "new path"},
      {"index": 7, "direction": "in", "name": "path"},  // From XML, not guessed
      {"index": 8, "direction": "in", "name": "error in"}
    ],
    "python": {"_body": "Path(path).mkdir(...)"}
  }

  Key rules:
  - parmIndex in XML = actual LabVIEW parameter index
  - Bit 0 of objFlags = isIndicator = OUTPUT terminal
  - Terminal names must match the variables used in python hints