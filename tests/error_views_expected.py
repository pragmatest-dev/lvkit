"""The text the views print for each construct of ``test_errors_as_values.py``.

Captured from the views as they are; they already show errors faithfully and do
not change in the errors-as-values work."""

from __future__ import annotations

# ruff: noqa: E501

EXPECTED: dict[str, dict[str, str]] = {
    "bundle_error": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   status    : TF     required @0
    in   code      : I32    required @1
    in   source    : String required @2
    out  error out : Error  recommended @3
  block-diagram :
    function Bundle/Unbundle_By_Name_112 : Bundle/Unbundle By Name
      in   1      : Error  = no error
      in   status : TF     = status
      in   code   : I32    = code
      in   source : String = source
      out  0      : Error
    error out = Bundle/Unbundle_By_Name_112::0""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   status    : TF     @0
    in   code      : I32    @1
    in   source    : String @2
    out  error out : Error  @3
  block-diagram :
    function Bundle/Unbundle_By_Name_112 : Bundle/Unbundle By Name
      in   1      : Error  = no error
      in   status : TF     = status
      in   code   : I32    = code
      in   source : String = source
      out  0      : Error
    error out = Bundle/Unbundle_By_Name_112::0""",
        "describe": r"""# Err.vi

  err(status: TF, code: I32, source: String) -> error out: Error

## Properties
  lock_state: unlocked

## Inputs
  status: TF (required)
  code: I32 (required)
  source: String (required)

## Outputs
  error out: Error

## Constants
  (unnamed): Error = no error

## Operations
Bundle/Unbundle By Name""",
        "json": '[{"inputs":[{"net":{"bare":"no error","terminal":"no error"},"terminal":"1"},{"net":{"bare":"status","terminal":"status"},"terminal":"status"},{"net":{"bare":"code","terminal":"code"},"terminal":"code"},{"net":{"bare":"source","terminal":"source"},"terminal":"source"}],"kind":"instance","name":"Bundle/Unbundle By Name","outputs":[{"bare":"Bundle/Unbundle By Name.0","node":"Bundle/Unbundle By Name","terminal":"0"}],"qualified_name":"Bundle/Unbundle By Name","uid":"112"}]',
    },
    "catalog_node_runs_despite_error": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   file      : ByteStream refnum required @0
    in   error in  : Error             recommended @1
    out  path      : Path              recommended @2
    out  error out : Error             recommended @3
  block-diagram :
    function Close_File_120 : Close File
      in   error_in  : Error             = error in
      in   refnum    : ByteStream refnum = file
      out  error_out : Error
      out  path      : Path
    path = Close_File_120::path
    error out = Close_File_120::error_out""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   file      : ByteStream refnum @0
    in   error in  : Error             @1
    out  path      : Path              @2
    out  error out : Error             @3
  block-diagram :
    function Close_File_120 : Close File
      in   error_in  : Error             = error in
      in   refnum    : ByteStream refnum = file
      out  error_out : Error
      out  path      : Path
    path = Close_File_120::path
    error out = Close_File_120::error_out""",
        "describe": r"""# Err.vi

  err(file: ByteStream refnum, error in: Error) -> path: Path, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  file: ByteStream refnum (required)
  error in: Error

## Outputs
  path: Path
  error out: Error

## Operations
Close File [prim 8052]""",
        "json": '[{"inputs":[{"net":{"bare":"error in","terminal":"error in"},"terminal":"error_in"},{"net":{"bare":"file","terminal":"file"},"terminal":"refnum"}],"kind":"instance","name":"Close File","outputs":[{"bare":"error_out","node":"Close File","terminal":"error_out"},{"bare":"path","node":"Close File","terminal":"path"}],"qualified_name":"Close File","uid":"120"}]',
    },
    "catalog_node_skips": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   file      : ByteStream refnum required @0
    in   error in  : Error             recommended @1
    out  size      : I64               recommended @2
    out  error out : Error             recommended @3
  block-diagram :
    function Get_File_Size_122 : Get File Size
      in   error_in   : Error             = error in
      in   refnum     : ByteStream refnum = file
      out  error_out  : Error
      out  size       : I64
      out  refnum_out : ByteStream refnum
    size = Get_File_Size_122::size
    error out = Get_File_Size_122::error_out""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   file      : ByteStream refnum @0
    in   error in  : Error             @1
    out  size      : I64               @2
    out  error out : Error             @3
  block-diagram :
    function Get_File_Size_122 : Get File Size
      in   error_in   : Error             = error in
      in   refnum     : ByteStream refnum = file
      out  error_out  : Error
      out  size       : I64
      out  refnum_out : ByteStream refnum
    size = Get_File_Size_122::size
    error out = Get_File_Size_122::error_out""",
        "describe": r"""# Err.vi

  err(file: ByteStream refnum, error in: Error) -> size: I64, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  file: ByteStream refnum (required)
  error in: Error

## Outputs
  size: I64
  error out: Error

## Operations
Get File Size [prim 8063]""",
        "json": '[{"inputs":[{"net":{"bare":"error in","terminal":"error in"},"terminal":"error_in"},{"net":{"bare":"file","terminal":"file"},"terminal":"refnum"}],"kind":"instance","name":"Get File Size","outputs":[{"bare":"error_out","node":"Get File Size","terminal":"error_out"},{"bare":"size","node":"Get File Size","terminal":"size"},{"bare":"refnum_out","node":"Get File Size","terminal":"refnum_out"}],"qualified_name":"Get File Size","uid":"122"}]',
    },
    "clear_errors": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Clear Errors.vi
    subVI Step A.vi       ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
    subVI Step B.vi       ; ./Step B.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Clear_Errors_138 : Clear Errors.vi
      in   specific error code to clear (0: all) : I32   default 0
      in   error in (no error)     : Error = Step_A_120::error out
      out  specific error cleared? : TF
      out  error out               : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = Clear_Errors_138::error out
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Clear Errors.vi
    subVI Step A.vi       ; ./Step A.vi
    subVI Step B.vi       ; ./Step B.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Clear_Errors_138 : Clear Errors.vi
      in   specific error code to clear (0: all) : I32   default 0
      in   error in (no error)     : Error = Step_A_120::error out
      out  specific error cleared? : TF
      out  error out               : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = Clear_Errors_138::error out
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Dependencies
  Clear Errors.vi: (error in (no error): None, specific error code to clear (0: all): None) -> (error out: None, specific error cleared?: None) -- Resets the error status to no error, code to 0, and source to an empty string. Use this VI when y...
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)
  Step B.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out
Clear Errors.vi
Step B.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"120"},{"inputs":[{"net":{"bare":"error out","node":"Step A.vi","terminal":"error out"},"terminal":"error in (no error)"}],"kind":"instance","name":"Clear Errors.vi","outputs":[{"bare":"error out","node":"Clear Errors.vi","terminal":"error out"},{"bare":"specific error cleared?","node":"Clear Errors.vi","terminal":"specific error cleared?"}],"qualified_name":"Clear Errors.vi","uid":"138"},{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error out","node":"Clear Errors.vi","terminal":"error out"},"terminal":"error in"}],"kind":"instance","name":"Step B.vi","outputs":[{"bare":"r","node":"Step B.vi","terminal":"r"},{"bare":"error out","node":"Step B.vi","terminal":"error out"}],"qualified_name":"Step B.vi","uid":"129"}]',
    },
    "clear_errors_code": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Clear Errors.vi
    subVI Step A.vi       ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
    subVI Step B.vi       ; ./Step B.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Clear_Errors_138 : Clear Errors.vi
      in   specific error code to clear (0: all) : I32   = 7
      in   error in (no error)     : Error = Step_A_120::error out
      out  specific error cleared? : TF
      out  error out               : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = Clear_Errors_138::error out
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Clear Errors.vi
    subVI Step A.vi       ; ./Step A.vi
    subVI Step B.vi       ; ./Step B.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Clear_Errors_138 : Clear Errors.vi
      in   specific error code to clear (0: all) : I32   = 7
      in   error in (no error)     : Error = Step_A_120::error out
      out  specific error cleared? : TF
      out  error out               : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = Clear_Errors_138::error out
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Constants
  (unnamed): I32 = 7

## Dependencies
  Clear Errors.vi: (error in (no error): None, specific error code to clear (0: all): None) -> (error out: None, specific error cleared?: None) -- Resets the error status to no error, code to 0, and source to an empty string. Use this VI when y...
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)
  Step B.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out
Clear Errors.vi
Step B.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"120"},{"inputs":[{"net":{"bare":"error out","node":"Step A.vi","terminal":"error out"},"terminal":"error in (no error)"},{"net":{"bare":"7","terminal":"7"},"terminal":"specific error code to clear (0: all)"}],"kind":"instance","name":"Clear Errors.vi","outputs":[{"bare":"error out","node":"Clear Errors.vi","terminal":"error out"},{"bare":"specific error cleared?","node":"Clear Errors.vi","terminal":"specific error cleared?"}],"qualified_name":"Clear Errors.vi","uid":"138"},{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error out","node":"Clear Errors.vi","terminal":"error out"},"terminal":"error in"}],"kind":"instance","name":"Step B.vi","outputs":[{"bare":"r","node":"Step B.vi","terminal":"r"},{"bare":"error out","node":"Step B.vi","terminal":"error out"}],"qualified_name":"Step B.vi","uid":"129"}]',
    },
    "error_case_codes": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    case Step_A_120::error out (id 121) :
      frame "No Error" :
        case_121::out0 = 1
      frame "Error 7" :
        case_121::out0 = 100
      frame default :
        case_121::out0 = 200
    result = case_121::out0
    error out = Step_A_120::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    case Step_A_120::error out :
      frame "No Error" :
        case_121::out0 = 1
      frame "Error 7" :
        case_121::out0 = 100
      frame default :
        case_121::out0 = 200
    result = case_121::out0
    error out = Step_A_120::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Control Flow
  Case structure (3 frames, gated on Step A.vi.error out)

## Operations
Step A.vi(x, error in) -> r, error out
Case Structure (3 frames)
  Frame "No Error":
    (unnamed): I32 = 1
  Frame "Error 7":
    (unnamed): I32 = 100
  Frame "Default":
    (unnamed): I32 = 200""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"120"},{"frames":[{"label":"No Error","passthrough":true,"value":"0"},{"label":"Error 7","passthrough":true,"value":"7"},{"is_default":true,"label":"Default","passthrough":true,"value":"Default"}],"kind":"scope","outputs":[{"cases":[{"frame":"No Error","source":{"bare":"1","terminal":"1"}},{"frame":"Error 7","source":{"bare":"100","terminal":"100"}},{"frame":"default","source":{"bare":"200","terminal":"200"}}],"kind":"tunnel","net":"case_121.out0","selector":{"bare":"error out","node":"Step A.vi","terminal":"error out"}}],"scope_kind":"case","selector":{"bare":"error out","node":"Step A.vi","terminal":"error out"},"uid":"121"}]',
    },
    "error_case_panel_fed": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    case error in (id 112) :
      frame "No Error" :
        function Increment_133 : Increment
          in   x   : I32 = x
          out  x+1 : I32
        case_112::out0 = Increment_133::x+1
      frame default :
        case_112::out0 = 100
    result = case_112::out0
    error out = error in""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    case error in :
      frame "No Error" :
        function Increment_133 : Increment
          in   x   : I32 = x
          out  x+1 : I32
        case_112::out0 = Increment_133::x+1
      frame default :
        case_112::out0 = 100
    result = case_112::out0
    error out = error in""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Control Flow
  Case structure (2 frames, gated on error in)

## Operations
Case Structure (2 frames)
  Frame "No Error":
    Increment [prim 1057]
  Frame "Default":
    (unnamed): I32 = 100""",
        "json": '[{"frames":[{"body":[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"}],"kind":"instance","name":"Increment","outputs":[{"bare":"x+1","node":"Increment","terminal":"x+1"}],"qualified_name":"Increment","uid":"133"}],"label":"No Error","passthrough":true,"value":"0"},{"is_default":true,"label":"Default","passthrough":true,"value":"Default"}],"kind":"scope","outputs":[{"cases":[{"frame":"No Error","source":{"bare":"x+1","node":"Increment","terminal":"x+1"}},{"frame":"default","source":{"bare":"100","terminal":"100"}}],"kind":"tunnel","net":"case_112.out0","selector":{"bare":"error in","terminal":"error in"}}],"scope_kind":"case","selector":{"bare":"error in","terminal":"error in"},"uid":"112"}]',
    },
    "error_cluster_from_error_code": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Error Cluster From Error Code.vi
  front-panel :
    pattern : 4800
    in   code      : I32   required @0
    out  error out : Error recommended @1
  block-diagram :
    subVI Error_Cluster_From_Error_Code_118 : Error Cluster From Error Code.vi
      in   is warning? (False)      : TF     default False
      in   show call chain? (False) : TF     default False
      in   error message ("")       : String default ""
      in   error code (0)           : I32    = code
      in   error in (no error)      : Error  default
      out  error out                : Error
    error out = Error_Cluster_From_Error_Code_118::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Error Cluster From Error Code.vi
  front-panel :
    pattern : 4800
    in   code      : I32   @0
    out  error out : Error @1
  block-diagram :
    subVI Error_Cluster_From_Error_Code_118 : Error Cluster From Error Code.vi
      in   is warning? (False)      : TF     default False
      in   show call chain? (False) : TF     default False
      in   error message ("")       : String default ""
      in   error code (0)           : I32    = code
      in   error in (no error)      : Error  default
      out  error out                : Error
    error out = Error_Cluster_From_Error_Code_118::error out""",
        "describe": r"""# Err.vi

  err(code: I32) -> error out: Error

## Properties
  lock_state: unlocked

## Inputs
  code: I32 (required)

## Outputs
  error out: Error

## Dependencies
  Error Cluster From Error Code.vi: (is warning? (False): Boolean, show call chain? (False): Boolean, error in (no error): Cluster, error message (""): String, error code (0): NumInt32) -> (error out: Cluster) -- Converts an error or warning code to an error cluster. This VI is useful when you receive a retur...

## Operations
Error Cluster From Error Code.vi""",
        "json": '[{"inputs":[{"net":{"bare":"code","terminal":"code"},"terminal":"error code (0)"}],"kind":"instance","name":"Error Cluster From Error Code.vi","outputs":[{"bare":"error out","node":"Error Cluster From Error Code.vi","terminal":"error out"}],"qualified_name":"Error Cluster From Error Code.vi","uid":"118"}]',
    },
    "error_cluster_from_warning_code": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Error Cluster From Error Code.vi
  front-panel :
    pattern : 4800
    in   code      : I32   required @0
    out  error out : Error recommended @1
  block-diagram :
    subVI Error_Cluster_From_Error_Code_118 : Error Cluster From Error Code.vi
      in   is warning? (False)      : TF     = True
      in   show call chain? (False) : TF     default False
      in   error message ("")       : String default ""
      in   error code (0)           : I32    = code
      in   error in (no error)      : Error  default
      out  error out                : Error
    error out = Error_Cluster_From_Error_Code_118::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Error Cluster From Error Code.vi
  front-panel :
    pattern : 4800
    in   code      : I32   @0
    out  error out : Error @1
  block-diagram :
    subVI Error_Cluster_From_Error_Code_118 : Error Cluster From Error Code.vi
      in   is warning? (False)      : TF     = True
      in   show call chain? (False) : TF     default False
      in   error message ("")       : String default ""
      in   error code (0)           : I32    = code
      in   error in (no error)      : Error  default
      out  error out                : Error
    error out = Error_Cluster_From_Error_Code_118::error out""",
        "describe": r"""# Err.vi

  err(code: I32) -> error out: Error

## Properties
  lock_state: unlocked

## Inputs
  code: I32 (required)

## Outputs
  error out: Error

## Constants
  (unnamed): TF = True

## Dependencies
  Error Cluster From Error Code.vi: (is warning? (False): Boolean, show call chain? (False): Boolean, error in (no error): Cluster, error message (""): String, error code (0): NumInt32) -> (error out: Cluster) -- Converts an error or warning code to an error cluster. This VI is useful when you receive a retur...

## Operations
Error Cluster From Error Code.vi""",
        "json": '[{"inputs":[{"net":{"bare":"True","terminal":"True"},"terminal":"is warning? (False)"},{"net":{"bare":"code","terminal":"code"},"terminal":"error code (0)"}],"kind":"instance","name":"Error Cluster From Error Code.vi","outputs":[{"bare":"error out","node":"Error Cluster From Error Code.vi","terminal":"error out"}],"qualified_name":"Error Cluster From Error Code.vi","uid":"118"}]',
    },
    "error_constant_error": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    out  error out : Error recommended @0
  block-diagram :
    error out = code 5""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    out  error out : Error @0
  block-diagram :
    error out = code 5""",
        "describe": r"""# Err.vi

  err() -> error out: Error

## Properties
  lock_state: unlocked

## Inputs
  (none)

## Outputs
  error out: Error

## Constants
  (unnamed): Error = code 5

## Operations""",
        "json": '[]',
    },
    "error_constant_none": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    out  error out : Error recommended @0
  block-diagram :
    error out = no error""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    out  error out : Error @0
  block-diagram :
    error out = no error""",
        "describe": r"""# Err.vi

  err() -> error out: Error

## Properties
  lock_state: unlocked

## Inputs
  (none)

## Outputs
  error out: Error

## Constants
  (unnamed): Error = no error

## Operations""",
        "json": '[]',
    },
    "error_constant_warning": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    out  error out : Error recommended @0
  block-diagram :
    error out = code 5""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    out  error out : Error @0
  block-diagram :
    error out = code 5""",
        "describe": r"""# Err.vi

  err() -> error out: Error

## Properties
  lock_state: unlocked

## Inputs
  (none)

## Outputs
  error out: Error

## Constants
  (unnamed): Error = code 5

## Operations""",
        "json": '[]',
    },
    "error_local_variable": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
    out  saved : Error
  block-diagram :
    local-variable saved_115 : write = error in
    local-variable saved_118 : read
    subVI Step_A_129 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = saved_118::saved
      out  r         : I32
      out  error out : Error
    result = Step_A_129::r
    error out = Step_A_129::error out
    saved = saved_115::saved""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
    out  saved : Error
  block-diagram :
    local-variable saved_115 : write = error in
    local-variable saved_118 : read
    subVI Step_A_129 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = saved_118::saved
      out  r         : I32
      out  error out : Error
    result = Step_A_129::r
    error out = Step_A_129::error out
    saved = saved_115::saved""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Front Panel (not on the connector pane)
  Indicators
    saved: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"error in","terminal":"error in"},"terminal":"saved"}],"kind":"instance","name":"saved","occurrence":1,"qualified_name":"saved","uid":"115"},{"kind":"instance","name":"saved","occurrence":2,"outputs":[{"bare":"saved","node":"saved","occurrence":2,"terminal":"saved"}],"qualified_name":"saved","uid":"118"},{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"saved","node":"saved","occurrence":2,"terminal":"saved"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"129"}]',
    },
    "error_loop_tunnel": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    for-loop (id 112) :
      loop_112::N = 1
      subVI Step_A_134 : Step A.vi
        in   x         : I32   = 1
        in   error in  : Error = error in
        out  r         : I32
        out  error out : Error
      tunnel loop_112::out0 : last-value = Step_A_134::error out
    result = x
    error out = loop_112::out0""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    for-loop :
      loop_112::N = 1
      subVI Step_A_134 : Step A.vi
        in   x         : I32   = 1
        in   error in  : Error = error in
        out  r         : I32
        out  error out : Error
      tunnel loop_112::out0 : last-value = Step_A_134::error out
    result = x
    error out = loop_112::out0""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Constants
  (unnamed): I32 = 1

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Control Flow
  For loop

## Operations
For Loop
  Step A.vi(x, error in) -> r, error out
  (unnamed): I32 = 1
  Tunnels:
    in  count terminal
    in  tunnel (error in)
    out last value tunnel (error out)""",
        "json": '[{"count":{"bare":"1","terminal":"1"},"frames":[{"body":[{"inputs":[{"net":{"bare":"1","terminal":"1"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"134"}],"label":"","passthrough":true,"value":""}],"kind":"scope","outputs":[{"kind":"tunnel","mode":"last_value","net":"loop_112.out0","value":{"bare":"error out","node":"Step A.vi","terminal":"error out"}}],"scope_kind":"for","tunnels":[{"direction":"input","kind":"count_terminal"},{"direction":"input","kind":"tunnel","mode":"passthrough"},{"direction":"output","kind":"tunnel","mode":"last_value"}],"uid":"112"}]',
    },
    "error_shift_register": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    for-loop (id 112) :
      loop_112::N = 3
      subVI Step_A_134 : Step A.vi
        in   x         : I32   = 1
        in   error in  : Error = loop_112::shift0
        out  r         : I32
        out  error out : Error
      shift-register loop_112::shift0 :
        init = error in
        each = Step_A_134::error out
    result = x
    error out = loop_112::shift0""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    for-loop :
      loop_112::N = 3
      subVI Step_A_134 : Step A.vi
        in   x         : I32   = 1
        in   error in  : Error = loop_112::shift0
        out  r         : I32
        out  error out : Error
      shift-register loop_112::shift0 :
        init = error in
        each = Step_A_134::error out
    result = x
    error out = loop_112::shift0""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Constants
  (unnamed): I32 = 3

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Control Flow
  For loop

## Operations
For Loop
  Step A.vi(x, error in) -> r, error out
  (unnamed): I32 = 1
  Tunnels:
    in  count terminal
    in  shift register (error in)
    out shift register (error out)""",
        "json": '[{"count":{"bare":"3","terminal":"3"},"frames":[{"body":[{"inputs":[{"net":{"bare":"1","terminal":"1"},"terminal":"x"},{"net":{"bare":"loop_112.shift0","terminal":"loop_112.shift0"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"134"}],"label":"","passthrough":true,"value":""}],"kind":"scope","outputs":[{"initial_value":{"bare":"error in","terminal":"error in"},"kind":"shift_register","net":"loop_112.shift0","next_value":{"bare":"error out","node":"Step A.vi","terminal":"error out"}}],"scope_kind":"for","tunnels":[{"direction":"input","kind":"count_terminal"},{"direction":"input","kind":"shift_register","sr_initialized":true},{"direction":"output","kind":"shift_register"}],"uid":"112"}]',
    },
    "forked_error": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
    subVI Step B.vi ; ./Step B.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x           : I32   required @0
    in   error in    : Error recommended @1
    out  result a    : I32   recommended @2
    out  result b    : I32   recommended @4
    out  error out a : Error recommended @3
    out  error out b : Error recommended @5
  block-diagram :
    subVI Step_A_126 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_135 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    result a = Step_A_126::r
    result b = Step_B_135::r
    error out a = Step_A_126::error out
    error out b = Step_B_135::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
    subVI Step B.vi ; ./Step B.vi
  front-panel :
    pattern : 4800
    in   x           : I32   @0
    in   error in    : Error @1
    out  result a    : I32   @2
    out  result b    : I32   @4
    out  error out a : Error @3
    out  error out b : Error @5
  block-diagram :
    subVI Step_A_126 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_135 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    result a = Step_A_126::r
    result b = Step_B_135::r
    error out a = Step_A_126::error out
    error out b = Step_B_135::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result a: I32, result b: I32, error out a: Error, error out b: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result a: I32
  result b: I32
  error out a: Error
  error out b: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)
  Step B.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out
Step B.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"126"},{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step B.vi","outputs":[{"bare":"r","node":"Step B.vi","terminal":"r"},{"bare":"error out","node":"Step B.vi","terminal":"error out"}],"qualified_name":"Step B.vi","uid":"135"}]',
    },
    "loop_stops_on_error": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    while-loop (stop if true) (id 112) :
      subVI Step_A_128 : Step A.vi
        in   x         : I32   = 1
        in   error in  : Error default
        out  r         : I32
        out  error out : Error
      loop_112::condition = Step_A_128::error out
      tunnel loop_112::out0 : last-value = Step_A_128::error out
    result = x
    error out = loop_112::out0""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    while-loop (stop if true) :
      subVI Step_A_128 : Step A.vi
        in   x         : I32   = 1
        in   error in  : Error default
        out  r         : I32
        out  error out : Error
      loop_112::condition = Step_A_128::error out
      tunnel loop_112::out0 : last-value = Step_A_128::error out
    result = x
    error out = loop_112::out0""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Control Flow
  While loop (stops when Step A.vi.error out)

## Operations
While Loop
  Step A.vi(x, error in) -> r, error out
  (unnamed): I32 = 1
  Tunnels:
    out last value tunnel (error out)""",
        "json": '[{"condition":"stop_if_true","frames":[{"body":[{"inputs":[{"net":{"bare":"1","terminal":"1"},"terminal":"x"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"128"}],"label":"","passthrough":true,"value":""}],"kind":"scope","outputs":[{"kind":"tunnel","mode":"last_value","net":"loop_112.out0","value":{"bare":"error out","node":"Step A.vi","terminal":"error out"}}],"scope_kind":"while","selector":{"bare":"error out","node":"Step A.vi","terminal":"error out"},"tunnels":[{"direction":"output","kind":"tunnel","mode":"last_value"}],"uid":"112"}]',
    },
    "merge_errors": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
    subVI Step B.vi ; ./Step B.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
    subVI Step C.vi ; ./Step C.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    function Merge_Errors_139 : Merge Errors
      in   error in  : Error = Step_A_120::error out
      in   error in  : Error = Step_B_129::error out
      out  error out : Error
    subVI Step_C_138 : Step C.vi
      in   x         : I32   = x
      in   error in  : Error = Merge_Errors_139::error out
      out  r         : I32
      out  error out : Error
    result = Step_C_138::r
    error out = Step_C_138::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
    subVI Step B.vi ; ./Step B.vi
    subVI Step C.vi ; ./Step C.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    function Merge_Errors_139 : Merge Errors
      in   error in  : Error = Step_A_120::error out
      in   error in  : Error = Step_B_129::error out
      out  error out : Error
    subVI Step_C_138 : Step C.vi
      in   x         : I32   = x
      in   error in  : Error = Merge_Errors_139::error out
      out  r         : I32
      out  error out : Error
    result = Step_C_138::r
    error out = Step_C_138::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)
  Step B.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)
  Step C.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out
Step B.vi(x, error in) -> r, error out
Merge Errors [prim 2147]
Step C.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"120"},{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step B.vi","outputs":[{"bare":"r","node":"Step B.vi","terminal":"r"},{"bare":"error out","node":"Step B.vi","terminal":"error out"}],"qualified_name":"Step B.vi","uid":"129"},{"inputs":[{"net":{"bare":"error out","node":"Step A.vi","terminal":"error out"},"terminal":"error in"},{"net":{"bare":"error out","node":"Step B.vi","terminal":"error out"},"terminal":"error in"}],"kind":"instance","name":"Merge Errors","outputs":[{"bare":"error out","node":"Merge Errors","terminal":"error out"}],"qualified_name":"Merge Errors","uid":"139"},{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error out","node":"Merge Errors","terminal":"error out"},"terminal":"error in"}],"kind":"instance","name":"Step C.vi","outputs":[{"bare":"r","node":"Step C.vi","terminal":"r"},{"bare":"error out","node":"Step C.vi","terminal":"error out"}],"qualified_name":"Step C.vi","uid":"138"}]',
    },
    "native_failure": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   path      : Path              required @0
    in   error in  : Error             recommended @1
    out  refnum    : ByteStream refnum recommended @2
    out  error out : Error             recommended @3
  block-diagram :
    function Open/Create/Replace_File_122 : Open/Create/Replace File
      in   error_in  : Error             = error in
      in   file_path : Path              = path
      out  error_out : Error
      out  cancelled : TF
      out  refnum    : ByteStream refnum
    refnum = Open/Create/Replace_File_122::refnum
    error out = Open/Create/Replace_File_122::error_out""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   path      : Path              @0
    in   error in  : Error             @1
    out  refnum    : ByteStream refnum @2
    out  error out : Error             @3
  block-diagram :
    function Open/Create/Replace_File_122 : Open/Create/Replace File
      in   error_in  : Error             = error in
      in   file_path : Path              = path
      out  error_out : Error
      out  cancelled : TF
      out  refnum    : ByteStream refnum
    refnum = Open/Create/Replace_File_122::refnum
    error out = Open/Create/Replace_File_122::error_out""",
        "describe": r"""# Err.vi

  err(path: Path, error in: Error) -> refnum: ByteStream refnum, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  path: Path (required)
  error in: Error

## Outputs
  refnum: ByteStream refnum
  error out: Error

## Operations
Open/Create/Replace File [prim 8050]""",
        "json": '[{"inputs":[{"net":{"bare":"error in","terminal":"error in"},"terminal":"error_in"},{"net":{"bare":"path","terminal":"path"},"terminal":"file_path"}],"kind":"instance","name":"Open/Create/Replace File","outputs":[{"bare":"error_out","node":"Open/Create/Replace File","terminal":"error_out"},{"bare":"cancelled","node":"Open/Create/Replace File","terminal":"cancelled"},{"bare":"refnum","node":"Open/Create/Replace File","terminal":"refnum"}],"qualified_name":"Open/Create/Replace File","uid":"122"}]',
    },
    "simple_error_handler": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Simple Error Handler.vi
  front-panel :
    pattern : 4800
    in   error in  : Error recommended @0
    out  failed    : TF    recommended @1
    out  code      : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Simple_Error_Handler_130 : Simple Error Handler.vi
      in   error code (no error:0)   : I32    default 0
      in   error source (" ")        : String default " "
      in   type of dialog (OK msg:1) : I32    default 1
      in   error in (no error)       : Error  = error in
      out  error?                    : TF
      out  code out                  : I32
      out  source out                : String
      out  message                   : String
      out  error out                 : Error
    failed = Simple_Error_Handler_130::error?
    code = Simple_Error_Handler_130::code out
    error out = Simple_Error_Handler_130::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Simple Error Handler.vi
  front-panel :
    pattern : 4800
    in   error in  : Error @0
    out  failed    : TF    @1
    out  code      : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Simple_Error_Handler_130 : Simple Error Handler.vi
      in   error code (no error:0)   : I32    default 0
      in   error source (" ")        : String default " "
      in   type of dialog (OK msg:1) : I32    default 1
      in   error in (no error)       : Error  = error in
      out  error?                    : TF
      out  code out                  : I32
      out  source out                : String
      out  message                   : String
      out  error out                 : Error
    failed = Simple_Error_Handler_130::error?
    code = Simple_Error_Handler_130::code out
    error out = Simple_Error_Handler_130::error out""",
        "describe": r"""# Err.vi

  err(error in: Error) -> failed: TF, code: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  error in: Error

## Outputs
  failed: TF
  code: I32
  error out: Error

## Dependencies
  Simple Error Handler.vi: (error code (no error:0): None, error source (" "): None, type of dialog (OK msg:1): NumUInt16, error in (no error): None) -> (error?: None, code out: None, source out: None, error out: Cluster, message: None) -- Indicates whether an error occurred. If an error occurred, this VI returns a description of the e...

## Operations
Simple Error Handler.vi""",
        "json": '[{"inputs":[{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in (no error)"}],"kind":"instance","name":"Simple Error Handler.vi","outputs":[{"bare":"error?","node":"Simple Error Handler.vi","terminal":"error?"},{"bare":"code out","node":"Simple Error Handler.vi","terminal":"code out"},{"bare":"source out","node":"Simple Error Handler.vi","terminal":"source out"},{"bare":"error out","node":"Simple Error Handler.vi","terminal":"error out"},{"bare":"message","node":"Simple Error Handler.vi","terminal":"message"}],"qualified_name":"Simple Error Handler.vi","uid":"130"}]',
    },
    "status_selects_case": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   error in : Error recommended @0
    out  result   : I32   recommended @1
  block-diagram :
    function Bundle/Unbundle_By_Name_106 : Bundle/Unbundle By Name
      in   0      : Error = error in
      out  status : TF
    case Bundle/Unbundle_By_Name_106::status (id 111) :
      frame "False" :
        case_111::out0 = 1
      frame "True" :
        case_111::out0 = 100
    result = case_111::out0""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   error in : Error @0
    out  result   : I32   @1
  block-diagram :
    function Bundle/Unbundle_By_Name_106 : Bundle/Unbundle By Name
      in   0      : Error = error in
      out  status : TF
    case Bundle/Unbundle_By_Name_106::status :
      frame "False" :
        case_111::out0 = 1
      frame "True" :
        case_111::out0 = 100
    result = case_111::out0""",
        "describe": r"""# Err.vi

  err(error in: Error) -> result: I32

## Properties
  lock_state: unlocked

## Inputs
  error in: Error

## Outputs
  result: I32

## Control Flow
  Case structure (2 frames, gated on Bundle/Unbundle By Name.status)

## Operations
Bundle/Unbundle By Name
Case Structure (2 frames)
  Frame "False":
    (unnamed): I32 = 1
  Frame "True":
    (unnamed): I32 = 100""",
        "json": '[{"inputs":[{"net":{"bare":"error in","terminal":"error in"},"terminal":"0"}],"kind":"instance","name":"Bundle/Unbundle By Name","outputs":[{"bare":"status","node":"Bundle/Unbundle By Name","terminal":"status"}],"qualified_name":"Bundle/Unbundle By Name","uid":"106"},{"frames":[{"label":"False","passthrough":true,"value":"False"},{"label":"True","passthrough":true,"value":"True"}],"kind":"scope","outputs":[{"cases":[{"frame":"False","source":{"bare":"1","terminal":"1"}},{"frame":"True","source":{"bare":"100","terminal":"100"}}],"kind":"tunnel","net":"case_111.out0","selector":{"bare":"status","node":"Bundle/Unbundle By Name","terminal":"status"}}],"scope_kind":"case","selector":{"bare":"status","node":"Bundle/Unbundle By Name","terminal":"status"},"uid":"111"}]',
    },
    "subvi_call": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    result = Step_A_120::r
    error out = Step_A_120::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    result = Step_A_120::r
    error out = Step_A_120::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"120"}]',
    },
    "subvi_chain": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
    subVI Step B.vi ; ./Step B.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = Step_A_120::r
      in   error in  : Error = Step_A_120::error out
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
    subVI Step B.vi ; ./Step B.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = Step_A_120::r
      in   error in  : Error = Step_A_120::error out
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)
  Step B.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out
Step B.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"120"},{"inputs":[{"net":{"bare":"r","node":"Step A.vi","terminal":"r"},"terminal":"x"},{"net":{"bare":"error out","node":"Step A.vi","terminal":"error out"},"terminal":"error in"}],"kind":"instance","name":"Step B.vi","outputs":[{"bare":"r","node":"Step B.vi","terminal":"r"},{"bare":"error out","node":"Step B.vi","terminal":"error out"}],"qualified_name":"Step B.vi","uid":"129"}]',
    },
    "unbundle_error": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   error in : Error recommended @0
    out  failed   : TF    recommended @1
    out  code     : I32   recommended @2
  block-diagram :
    function Bundle/Unbundle_By_Name_109 : Bundle/Unbundle By Name
      in   0      : Error = error in
      out  status : TF
      out  code   : I32
    failed = Bundle/Unbundle_By_Name_109::status
    code = Bundle/Unbundle_By_Name_109::code""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   error in : Error @0
    out  failed   : TF    @1
    out  code     : I32   @2
  block-diagram :
    function Bundle/Unbundle_By_Name_109 : Bundle/Unbundle By Name
      in   0      : Error = error in
      out  status : TF
      out  code   : I32
    failed = Bundle/Unbundle_By_Name_109::status
    code = Bundle/Unbundle_By_Name_109::code""",
        "describe": r"""# Err.vi

  err(error in: Error) -> failed: TF, code: I32

## Properties
  lock_state: unlocked

## Inputs
  error in: Error

## Outputs
  failed: TF
  code: I32

## Operations
Bundle/Unbundle By Name""",
        "json": '[{"inputs":[{"net":{"bare":"error in","terminal":"error in"},"terminal":"0"}],"kind":"instance","name":"Bundle/Unbundle By Name","outputs":[{"bare":"status","node":"Bundle/Unbundle By Name","terminal":"status"},{"bare":"code","node":"Bundle/Unbundle By Name","terminal":"code"}],"qualified_name":"Bundle/Unbundle By Name","uid":"109"}]',
    },
    "unwired_error_in": {
        "lvnet": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
    subVI Step B.vi ; ./Step B.vi
      in   x         : I32
      in   error in  : Error
      out  r         : I32
      out  error out : Error
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = Step_A_120::r
      in   error in  : Error default
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "terse": r"""vi Err.vi :
  uses :
    subVI Step A.vi ; ./Step A.vi
    subVI Step B.vi ; ./Step B.vi
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    subVI Step_A_120 : Step A.vi
      in   x         : I32   = x
      in   error in  : Error = error in
      out  r         : I32
      out  error out : Error
    subVI Step_B_129 : Step B.vi
      in   x         : I32   = Step_A_120::r
      in   error in  : Error default
      out  r         : I32
      out  error out : Error
    result = Step_B_129::r
    error out = Step_B_129::error out""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Dependencies
  Step A.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)
  Step B.vi: (x: I32, error in: Error) -> (r: I32, error out: Error)

## Operations
Step A.vi(x, error in) -> r, error out
Step B.vi(x, error in) -> r, error out""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"},{"net":{"bare":"error in","terminal":"error in"},"terminal":"error in"}],"kind":"instance","name":"Step A.vi","outputs":[{"bare":"r","node":"Step A.vi","terminal":"r"},{"bare":"error out","node":"Step A.vi","terminal":"error out"}],"qualified_name":"Step A.vi","uid":"120"},{"inputs":[{"net":{"bare":"r","node":"Step A.vi","terminal":"r"},"terminal":"x"}],"kind":"instance","name":"Step B.vi","outputs":[{"bare":"r","node":"Step B.vi","terminal":"r"},{"bare":"error out","node":"Step B.vi","terminal":"error out"}],"qualified_name":"Step B.vi","uid":"129"}]',
    },
    "vi_error_in_required": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error required @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    function Increment_116 : Increment
      in   x   : I32 = x
      out  x+1 : I32
    result = Increment_116::x+1
    error out = error in""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    function Increment_116 : Increment
      in   x   : I32 = x
      out  x+1 : I32
    result = Increment_116::x+1
    error out = error in""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error (required)

## Outputs
  result: I32
  error out: Error

## Operations
Increment [prim 1057]""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"}],"kind":"instance","name":"Increment","outputs":[{"bare":"x+1","node":"Increment","terminal":"x+1"}],"qualified_name":"Increment","uid":"116"}]',
    },
    "vi_error_terminals": {
        "lvnet": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   x         : I32   required @0
    in   error in  : Error recommended @1
    out  result    : I32   recommended @2
    out  error out : Error recommended @3
  block-diagram :
    function Increment_116 : Increment
      in   x   : I32 = x
      out  x+1 : I32
    result = Increment_116::x+1
    error out = error in""",
        "terse": r"""vi Err.vi :
  front-panel :
    pattern : 4800
    in   x         : I32   @0
    in   error in  : Error @1
    out  result    : I32   @2
    out  error out : Error @3
  block-diagram :
    function Increment_116 : Increment
      in   x   : I32 = x
      out  x+1 : I32
    result = Increment_116::x+1
    error out = error in""",
        "describe": r"""# Err.vi

  err(x: I32, error in: Error) -> result: I32, error out: Error

## Properties
  lock_state: unlocked

## Inputs
  x: I32 (required)
  error in: Error

## Outputs
  result: I32
  error out: Error

## Operations
Increment [prim 1057]""",
        "json": '[{"inputs":[{"net":{"bare":"x","terminal":"x"},"terminal":"x"}],"kind":"instance","name":"Increment","outputs":[{"bare":"x+1","node":"Increment","terminal":"x+1"}],"qualified_name":"Increment","uid":"116"}]',
    },
}
