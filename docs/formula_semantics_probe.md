# Formula Node semantics probe

Hand this to a LabVIEW user. It pins the **numeric model** the NI docs leave underspecified (integer width/overflow, rounding, division typing, mod sign, bitwise, special values). It must run in a real **Formula Node** (typed terminals) — the Eval Formula Node VI is real-numbers-only and can't show integer behaviour.

## Script 1 — main probe (required)

```c
/* lvkit Formula Node semantics probe — MAIN.
 * Create a Formula Node. Add two terminals wired as BOTH input and
 * output (same name on left and right):
 *   RI : int32 array,   >= 26 elements, initialized to 0
 *   RF : double array,  >= 15 elements, initialized to 0
 * Paste this script, run once, and paste the RI and RF arrays back. */
uInt8 u8;
int8 i8;
int16 i16;
int8 a;
int8 b;

RI[0] = 0.5;
RI[1] = 1.5;
RI[2] = 2.5;
RI[3] = -1.5;
u8 = 300; RI[4] = u8;
i8 = 200; RI[5] = i8;
i8 = -200; RI[6] = i8;
u8 = -1; RI[7] = u8;
i16 = 40000; RI[8] = i16;
a = 100; b = 100; RI[9] = a * b;
RI[10] = 7 / 2;
RI[11] = -7 / 2;
RI[12] = -7 % 3;
RI[13] = 7 % -3;
RI[14] = ~0;
RI[15] = -8 >> 1;
RI[16] = 1 << 31;
RI[17] = (3 > 2);
RI[18] = (2 > 3);
RI[19] = !5;
RI[20] = !0;
RI[21] = (2 && 3);
RI[22] = (0 || 0);
RI[23] = 2 ** 3;
RI[24] = sign(-3);
RI[25] = sign(0);
RF[0] = 7 / 2;
RF[1] = 1 / 3;
RF[2] = int(2.5);
RF[3] = int(3.5);
RF[4] = int(-2.5);
RF[5] = intrz(2.7);
RF[6] = intrz(-2.7);
RF[7] = mod(-7, 3);
RF[8] = mod(7.5, 2);
RF[9] = getexp(12.0);
RF[10] = getman(12.0);
RF[11] = 2 ** -1;
RF[12] = 2 ** 0.5;
RF[13] = max(2.0, 5.0);
RF[14] = rand();
```

## Script 2 — special values (run as a SEPARATE Formula Node)

These can trap at the node level (divide-by-zero, sqrt of a negative). Keeping them in their **own** Formula Node means a trap can't take Script 1's results down with it. If this node refuses to run or pops an error instead of producing `inf`/`nan`, **that is the answer** — report "traps" (and which line, by deleting them one at a time).

```c
/* lvkit Formula Node semantics probe — SPECIAL VALUES.
 * Put this in a SEPARATE Formula Node from the main one, so that if a
 * divide-by-zero or sqrt(-1) traps at the node level, it CANNOT void
 * the main probe's output. A trap here is itself a valid result.
 *   RX : double array,  >= 4 elements, initialized to 0 */
RX[0] = (-8) ** (1.0 / 3.0);
RX[1] = 1.0 / 0.0;
RX[2] = 0.0 / 0.0;
RX[3] = sqrt(-1.0);
```

## Reading the results back out of LabVIEW

The output goes through LabVIEW indicators, so the **display format** has to keep the distinguishing bits visible. Convert each array to text and paste it verbatim:

- On the block diagram, wire `RI` and `RF` through **Array To Spreadsheet String** (Programming » String) and copy the strings. Use these format strings:
  - `RF` (double): **`%.17g`** — full precision, and renders `inf` / `-inf` / `nan` literally.
  - `RI` (int32): **`%d`** — exact integers; wrap/overflow show directly (e.g. `44`, `-56`, `-2147483648`).

- For the **bit-level** rows — `RI[14]` (`~0`), `RI[15]` (`>>`), `RI[16]` (`1<<31`) — also add a second Array To Spreadsheet String on `RI` with **`0x%08X`** (or set the indicator radix to Hex), so the bit pattern is unambiguous, not just the signed decimal.

- Script 2's `RX` rows should come back as `inf` / `nan` if LabVIEW math is IEEE — but if that node traps instead, report "traps". Either outcome is a valid result.

- Change **only the indicator's display format**, never a value. If a single line stops the node from compiling, delete that one line, re-run, and tell us which line it was.

## Decoder — `our value` is what lvkit produces today; any LabVIEW value that differs is a rule to fix/confirm

| slot | expression | our value | LabVIEW says → rule |
|------|------------|-----------|---------------------|
| `RI[0]` | `0.5` | `{'r': 0}` | 0=ties-even · 1=half-up |
| `RI[1]` | `1.5` | `{'r': 2}` | 2=ties-even · 2=half-up |
| `RI[2]` | `2.5` | `{'r': 2}` | 2=ties-even · 3=half-up |
| `RI[3]` | `-1.5` | `{'r': -2}` | -2=ties-even · -2=half-away |
| `RI[4]` | `uInt8 u8; u8 = 300; u8` | `{'r': 44}` | 44=wrap · 255=saturate |
| `RI[5]` | `int8 i8; i8 = 200; i8` | `{'r': -56}` | -56=wrap · 127=saturate |
| `RI[6]` | `int8 i8; i8 = -200; i8` | `{'r': 56}` | 56=wrap · -128=saturate |
| `RI[7]` | `uInt8 u8; u8 = -1; u8` | `{'r': 255}` | 255=wrap · 0=saturate |
| `RI[8]` | `int16 i16; i16 = 40000; i16` | `{'r': -25536}` | -25536=wrap · 32767=saturate |
| `RI[9]` | `int8 a; int8 b; a = 100; b = 100; a * b` | `{'r': 10000}` | 10000=promote · 16=narrow-intermediate |
| `RI[10]` | `7 / 2` | `{'r': 4}` | 4=round · 3=trunc |
| `RI[11]` | `-7 / 2` | `{'r': -4}` | -4=round-even · -3=trunc |
| `RI[12]` | `-7 % 3` | `{'r': 2}` | -1=C-sign-of-dividend · 2=floored |
| `RI[13]` | `7 % -3` | `{'r': -2}` | 1=C-sign-of-dividend · -2=floored |
| `RI[14]` | `~0` | `{'r': -1}` | -1=twos-complement |
| `RI[15]` | `-8 >> 1` | `{'r': -4}` | -4=arithmetic-shift · 2147483644=logical |
| `RI[16]` | `1 << 31` | `{'r': -2147483648}` | -2147483648=wrap · 2147483648=widen |
| `RI[17]` | `(3 > 2)` | `{'r': 1}` | 1=int-1/0 |
| `RI[18]` | `(2 > 3)` | `{'r': 0}` | 0=int-1/0 |
| `RI[19]` | `!5` | `{'r': 0}` | 0=logical-not |
| `RI[20]` | `!0` | `{'r': 1}` | 1=logical-not |
| `RI[21]` | `(2 && 3)` | `{'r': 3}` | 1=int-1 · 3=value-of-operand |
| `RI[22]` | `(0 || 0)` | `{'r': 0}` | 0=int-0 |
| `RI[23]` | `2 ** 3` | `{'r': 8}` | 8=power |
| `RI[24]` | `sign(-3)` | `{'r': -1}` | -1=sign |
| `RI[25]` | `sign(0)` | `{'r': 0}` | 0=sign |
| `RF[0]` | `7 / 2` | `{'r': 3.5}` | 3.5=real-div · 3.0=trunc |
| `RF[1]` | `1 / 3` | `{'r': 0.3333333333333333}` | 0.3333333333333333=real-div · 0.0=trunc |
| `RF[2]` | `int(2.5)` | `{'r': 2}` | 2.0=ties-even · 3.0=half-up |
| `RF[3]` | `int(3.5)` | `{'r': 4}` | 4.0=ties-even |
| `RF[4]` | `int(-2.5)` | `{'r': -2}` | -2.0=ties-even |
| `RF[5]` | `intrz(2.7)` | `{'r': 2}` | 2.0=toward-zero |
| `RF[6]` | `intrz(-2.7)` | `{'r': -2}` | -2.0=toward-zero |
| `RF[7]` | `mod(-7, 3)` | `{'r': -1.0}` | -1.0=C-sign-of-dividend · 2.0=floored |
| `RF[8]` | `mod(7.5, 2)` | `{'r': 1.5}` | 1.5=fmod |
| `RF[9]` | `getexp(12.0)` | `UNSUPPORTED (unsupported function 'getexp')` | 3.0=IEEE · 4.0=frexp |
| `RF[10]` | `getman(12.0)` | `UNSUPPORTED (unsupported function 'getman')` | 1.5=IEEE-1.x · 0.75=frexp-0.x |
| `RF[11]` | `2 ** -1` | `{'r': 0.5}` | 0.5=power |
| `RF[12]` | `2 ** 0.5` | `{'r': 1.4142135623730951}` | 1.4142135623730951=sqrt2 |
| `RF[13]` | `max(2.0, 5.0)` | `{'r': 5.0}` | 5.0=max |
| `RF[14]` | `rand()` | `UNSUPPORTED (unsupported function 'rand')` | 0<=x<1=uniform [0,1) |
| `RX[0]` | `(-8) ** (1.0 / 3.0)` | `{'r': (1.0000000000000002+1.7320508075688772j)}` | nan=NaN-like-C-pow · -2.0=real-cube-root |
| `RX[1]` | `1.0 / 0.0` | `RUNTIME ZeroDivisionError: float division by zero` | inf=IEEE-inf · node errors=trap |
| `RX[2]` | `0.0 / 0.0` | `RUNTIME ZeroDivisionError: float division by zero` | nan=NaN · node errors=trap |
| `RX[3]` | `sqrt(-1.0)` | `RUNTIME ValueError: math domain error` | nan=NaN · node errors=trap |
