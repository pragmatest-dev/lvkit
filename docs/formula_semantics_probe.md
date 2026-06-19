# Formula Node semantics probe

A comprehensive probe (60 integer + 58 real + 12 special-value cases) that pins the numeric model the NI docs leave underspecified. Run it in a real **Formula Node** (typed terminals) — the Eval Formula Node VI is real-numbers-only and can't show integer behaviour.

## Script 1 — main probe (required)

```c
/* lvkit Formula Node semantics probe — MAIN.
 * Create a Formula Node with these terminals:
   *  A  : double array   (INPUT)  = [10,20,30,40,50]
   *  B  : double 2D array (INPUT)  = [[1,2,3],[4,5,6]]
   *  n  : int32          (INPUT)  = 5
   *  RI : int32 array  (in+out)  >= 60 elements, init 0
   *  RF : double array (in+out)  >= 58 elements, init 0
 * Paste this, run once, report RI and RF. */
uInt8 u8;
int8 i8;
uInt16 u16;
int16 i16;
uInt32 u32;
int8 a;
int8 b;
int16 a;
int16 b;
float32 s;

RI[0] = 0.5;
RI[1] = 1.5;
RI[2] = 2.5;
RI[3] = 3.5;
RI[4] = -0.5;
RI[5] = -1.5;
RI[6] = -2.5;
u8 = 300; RI[7] = u8;
u8 = -1; RI[8] = u8;
i8 = 200; RI[9] = i8;
i8 = -200; RI[10] = i8;
u16 = 70000; RI[11] = u16;
i16 = 40000; RI[12] = i16;
u32 = 5000000000; RI[13] = u32;
a = 100; b = 100; RI[14] = a * b;
a = 30000; b = 30000; RI[15] = a + b;
RI[16] = 7 / 2;
RI[17] = -7 / 2;
RI[18] = 5 / 2;
RF[0] = 7 / 2;
RF[1] = 1 / 3;
RI[19] = -7 % 3;
RI[20] = 7 % -3;
RF[2] = 7.5 % 2;
RF[3] = mod(-7, 3);
RF[4] = mod(7.5, 2);
RF[5] = rem(7, 3);
RF[6] = rem(-7, 3);
RI[21] = 12 & 10;
RI[22] = 12 | 10;
RI[23] = 12 ^ 10;
RI[24] = ~0;
RI[25] = ~5;
RI[26] = -1 & 255;
RI[27] = 1 << 4;
RI[28] = 1 << 31;
RI[29] = -8 >> 1;
RI[30] = 256 >> 2;
u8 = 200; RI[31] = u8 >> 1;
RI[32] = (3 > 2);
RI[33] = (2 > 3);
RI[34] = (5 == 5);
RI[35] = (5 != 5);
RI[36] = (3 <= 3);
RI[37] = (3 >= 4);
RF[7] = (3 > 2);
RF[8] = (0.1 + 0.2 == 0.3);
RI[38] = (5 && 3);
RI[39] = (5 && 0);
RI[40] = (0 || 5);
RI[41] = (0 || 0);
RI[42] = !5;
RI[43] = !0;
RI[44] = !!5;
RI[45] = 2 ** 3;
RI[46] = 2 ** 10;
RI[47] = (-2) ** 2;
RI[48] = (-2) ** 3;
RF[9] = 2 ** -1;
RF[10] = 2 ** 0.5;
RF[11] = 2 ** 0;
RI[49] = 1 + 2 * 3;
RI[50] = 2 ** 3 ** 2;
RI[51] = -2 ** 2;
RI[52] = 1 << 2 + 1;
RI[53] = 20 - 5 - 3;
RF[12] = 2 ** -2 ** 2;
RF[13] = sin(pi / 2);
RF[14] = acos(0.5);
RF[15] = atan(1.0);
RF[16] = cos(0.0);
RF[17] = tan(0.0);
RF[18] = asin(0.5);
RF[19] = atan2(1.0, 1.0);
RF[20] = sinh(0.0);
RF[21] = cosh(0.0);
RF[22] = tanh(0.0);
RF[23] = asinh(1.0);
RF[24] = acosh(2.0);
RF[25] = atanh(0.5);
RF[26] = cot(1.0);
RF[27] = csc(1.0);
RF[28] = sec(1.0);
RF[29] = sinc(1.0);
RF[30] = log(100.0);
RF[31] = ln(2.718281828);
RF[32] = log2(8.0);
RF[33] = exp(1.0);
RF[34] = expm1(0.0);
RF[35] = lnp1(0.0);
RF[36] = sqrt(2.0);
RF[37] = int(2.5);
RF[38] = int(-2.5);
RF[39] = intrz(2.7);
RF[40] = intrz(-2.7);
RF[41] = ceil(2.1);
RF[42] = floor(-2.1);
RF[43] = abs(-2.5);
RI[54] = abs(-3);
RF[44] = max(2.0, 5.0);
RF[45] = min(2.0, 5.0);
RI[55] = sign(-3);
RI[56] = sign(0);
RF[46] = pow(2.0, 10.0);
RF[47] = getexp(12.0);
RF[48] = getman(12.0);
RF[49] = getexp(0.75);
RF[50] = getman(0.75);
RF[51] = rand();
RI[57] = sizeOfDim(A, 0);
RF[52] = A[0];
RF[53] = A[n - 1];
RI[58] = sizeOfDim(B, 0);
RI[59] = sizeOfDim(B, 1);
RF[54] = B[1][2];
RF[55] = pi;
RF[56] = 1E3;
s = 1.0 / 3.0; RF[57] = s;
```

## Script 2 — special values (run as a SEPARATE Formula Node)

These can trap at the node level (divide-by-zero, sqrt of a negative, domain errors). A separate node means a trap can't take Script 1 down with it. If this node errors instead of producing `inf`/`nan`, **that is the answer** — report "traps" (delete lines one at a time to find which).

```c
/* lvkit Formula Node semantics probe — SPECIAL VALUES.
 * Put this in a SEPARATE Formula Node so a divide-by-zero / sqrt(-1)
 * trap can't void the MAIN run. A trap here is itself a valid result.
   *  RX : double array (in+out) >= 12 elements, init 0 */
RX[0] = 1 / 0;
RX[1] = 1.0 / 0.0;
RX[2] = -1.0 / 0.0;
RX[3] = 0.0 / 0.0;
RX[4] = sqrt(-1.0);
RX[5] = ln(0.0);
RX[6] = ln(-1.0);
RX[7] = log(0.0);
RX[8] = acos(2.0);
RX[9] = (-8) ** (1.0 / 3.0);
RX[10] = 0 ** 0;
RX[11] = tan(pi / 2);
```

## Reading the results back out of LabVIEW

Display format must keep the distinguishing bits visible. Wire each array through **Array To Spreadsheet String** and copy the text:
- `RF`/`RX` (double): format **`%.17g`** — full precision, and renders `inf` / `-inf` / `nan` literally.
- `RI` (int32): format **`%d`** — exact integers; wrap/overflow show directly. For the bitwise/shift rows, add a second `RI` string with **`0x%08X`** (or set the indicator radix to Hex) so the bit pattern is explicit, not just signed decimal.
- Change only the indicator's *display*, never a value. If one line stops the node compiling, delete it, re-run, and say which line.

## Decoder — `our value` is lvkit's current output; any LabVIEW value that differs is a rule to fix/confirm

| slot | expression | our value | LabVIEW says → rule |
|------|------------|-----------|---------------------|
| `RI[0]` | `0.5` | `0` | ties-even vs half-up vs half-away vs trunc |
| `RI[1]` | `1.5` | `2` | ties-even vs half-up vs half-away vs trunc |
| `RI[2]` | `2.5` | `2` | ties-even vs half-up vs half-away vs trunc |
| `RI[3]` | `3.5` | `4` | ties-even vs half-up vs half-away vs trunc |
| `RI[4]` | `-0.5` | `0` | ties-even vs half-up vs half-away vs trunc |
| `RI[5]` | `-1.5` | `-2` | ties-even vs half-up vs half-away vs trunc |
| `RI[6]` | `-2.5` | `-2` | ties-even vs half-up vs half-away vs trunc |
| `RI[7]` | `uInt8 u8; u8 = 300; u8` | `44` | 44=wrap 255=saturate |
| `RI[8]` | `uInt8 u8; u8 = -1; u8` | `255` | 255=wrap 0=saturate |
| `RI[9]` | `int8 i8; i8 = 200; i8` | `-56` | -56=wrap 127=saturate |
| `RI[10]` | `int8 i8; i8 = -200; i8` | `56` | 56=wrap -128=saturate |
| `RI[11]` | `uInt16 u16; u16 = 70000; u16` | `4464` | 4464=wrap 65535=saturate |
| `RI[12]` | `int16 i16; i16 = 40000; i16` | `-25536` | -25536=wrap 32767=saturate |
| `RI[13]` | `uInt32 u32; u32 = 5000000000; u32` | `705032704` | 705032704=wrap 4294967295=saturate |
| `RI[14]` | `int8 a; int8 b; a = 100; b = 100; a * b` | `10000` | 10000=promote 16=narrow-intermediate |
| `RI[15]` | `int16 a; int16 b; a = 30000; b = 30000; a + b` | `60000` | 60000=promote -5536=narrow-intermediate |
| `RI[16]` | `7 / 2` | `4` | 4=round 3=trunc |
| `RI[17]` | `-7 / 2` | `-4` | -4=round-even -3=trunc |
| `RI[18]` | `5 / 2` | `2` | 2=round-even 3=half-up |
| `RF[0]` | `7 / 2` | `3.5` | 3.5=real-div 3.0=trunc |
| `RF[1]` | `1 / 3` | `0.3333333333333333` | 0.333..=real-div 0.0=trunc |
| `RI[19]` | `-7 % 3` | `2` | -1=C-sign-of-dividend 2=floored |
| `RI[20]` | `7 % -3` | `-2` | 1=C-sign-of-dividend -2=floored |
| `RF[2]` | `7.5 % 2` | `1.5` | 1.5=fmod (does %% work on doubles?) |
| `RF[3]` | `mod(-7, 3)` | `-1.0` | -1=C 2=floored |
| `RF[4]` | `mod(7.5, 2)` | `1.5` | 1.5 |
| `RF[5]` | `rem(7, 3)` | `1` | 1=round-remainder |
| `RF[6]` | `rem(-7, 3)` | `-1` | -1=round-remainder |
| `RI[21]` | `12 & 10` | `8` | 8 |
| `RI[22]` | `12 \| 10` | `14` | 14 |
| `RI[23]` | `12 ^ 10` | `6` | 6 |
| `RI[24]` | `~0` | `-1` | -1=twos-complement |
| `RI[25]` | `~5` | `-6` | -6 |
| `RI[26]` | `-1 & 255` | `255` | 255=signed-and |
| `RI[27]` | `1 << 4` | `16` | 16 |
| `RI[28]` | `1 << 31` | `-2147483648` | -2147483648=wrap 2147483648=widen |
| `RI[29]` | `-8 >> 1` | `-4` | -4=arithmetic 2147483644=logical |
| `RI[30]` | `256 >> 2` | `64` | 64 |
| `RI[31]` | `uInt8 u8; u8 = 200; u8 >> 1` | `100` | 100=unsigned-logical-shift |
| `RI[32]` | `(3 > 2)` | `1` | 1=int-1/0 |
| `RI[33]` | `(2 > 3)` | `0` | 0 |
| `RI[34]` | `(5 == 5)` | `1` | 1 |
| `RI[35]` | `(5 != 5)` | `0` | 0 |
| `RI[36]` | `(3 <= 3)` | `1` | 1 |
| `RI[37]` | `(3 >= 4)` | `0` | 0 |
| `RF[7]` | `(3 > 2)` | `True` | 1.0=comparison stored to double |
| `RF[8]` | `(0.1 + 0.2 == 0.3)` | `False` | 0=float-eq-is-exact 1=tolerant |
| `RI[38]` | `(5 && 3)` | `3` | 1=logical 3=value-of-operand |
| `RI[39]` | `(5 && 0)` | `0` | 0 |
| `RI[40]` | `(0 \|\| 5)` | `5` | 1=logical 5=value-of-operand |
| `RI[41]` | `(0 \|\| 0)` | `0` | 0 |
| `RI[42]` | `!5` | `0` | 0 |
| `RI[43]` | `!0` | `1` | 1 |
| `RI[44]` | `!!5` | `1` | 1 |
| `RI[45]` | `2 ** 3` | `8` | 8 |
| `RI[46]` | `2 ** 10` | `1024` | 1024 |
| `RI[47]` | `(-2) ** 2` | `4` | 4 |
| `RI[48]` | `(-2) ** 3` | `-8` | -8 |
| `RF[9]` | `2 ** -1` | `0.5` | 0.5 |
| `RF[10]` | `2 ** 0.5` | `1.4142135623730951` | 1.41421.. |
| `RF[11]` | `2 ** 0` | `1` | 1 |
| `RI[49]` | `1 + 2 * 3` | `7` | 7=mul-first 9=left-to-right |
| `RI[50]` | `2 ** 3 ** 2` | `512` | 512=right-assoc 64=left-assoc |
| `RI[51]` | `-2 ** 2` | `-4` | -4=power>unary 4=unary>power |
| `RI[52]` | `1 << 2 + 1` | `8` | 8=add-first 5=shift-first |
| `RI[53]` | `20 - 5 - 3` | `12` | 12=left-assoc 18=right-assoc |
| `RF[12]` | `2 ** -2 ** 2` | `0.0625` | 0.0625=right 16=left |
| `RF[13]` | `sin(pi / 2)` | `1.0` | 1=radians ~0.027=degrees |
| `RF[14]` | `acos(0.5)` | `1.0471975511965979` | 1.047=radians 60=degrees |
| `RF[15]` | `atan(1.0)` | `0.7853981633974483` | 0.785=radians 45=degrees |
| `RF[16]` | `cos(0.0)` | `1.0` | 1 |
| `RF[17]` | `tan(0.0)` | `0.0` | 0 |
| `RF[18]` | `asin(0.5)` | `0.5235987755982989` | 0.5236=radians |
| `RF[19]` | `atan2(1.0, 1.0)` | `0.7853981633974483` | 0.785=radians |
| `RF[20]` | `sinh(0.0)` | `0.0` | 0 |
| `RF[21]` | `cosh(0.0)` | `1.0` | 1 |
| `RF[22]` | `tanh(0.0)` | `0.0` | 0 |
| `RF[23]` | `asinh(1.0)` | `0.881373587019543` | 0.8814 |
| `RF[24]` | `acosh(2.0)` | `1.3169578969248166` | 1.3170 |
| `RF[25]` | `atanh(0.5)` | `0.5493061443340548` | 0.5493 |
| `RF[26]` | `cot(1.0)` | `0.6420926159343306` | 0.6421 |
| `RF[27]` | `csc(1.0)` | `1.1883951057781212` | 1.1884 |
| `RF[28]` | `sec(1.0)` | `1.8508157176809255` | 1.8508 |
| `RF[29]` | `sinc(1.0)` | `0.8414709848078965` | 0.8415=unnormalized 0=normalized(sin(pi x)) |
| `RF[30]` | `log(100.0)` | `2.0` | 2=log10 4.605=natural |
| `RF[31]` | `ln(2.718281828)` | `0.9999999998311266` | 1=natural |
| `RF[32]` | `log2(8.0)` | `3.0` | 3 |
| `RF[33]` | `exp(1.0)` | `2.718281828459045` | 2.71828 |
| `RF[34]` | `expm1(0.0)` | `0.0` | 0 |
| `RF[35]` | `lnp1(0.0)` | `0.0` | 0 |
| `RF[36]` | `sqrt(2.0)` | `1.4142135623730951` | 1.41421 |
| `RF[37]` | `int(2.5)` | `2` | 2=ties-even 3=half-up |
| `RF[38]` | `int(-2.5)` | `-2` | -2=ties-even |
| `RF[39]` | `intrz(2.7)` | `2` | 2=toward-zero |
| `RF[40]` | `intrz(-2.7)` | `-2` | -2=toward-zero |
| `RF[41]` | `ceil(2.1)` | `3` | 3 |
| `RF[42]` | `floor(-2.1)` | `-3` | -3 |
| `RF[43]` | `abs(-2.5)` | `2.5` | 2.5 |
| `RI[54]` | `abs(-3)` | `3` | 3=polymorphic-int |
| `RF[44]` | `max(2.0, 5.0)` | `5.0` | 5 |
| `RF[45]` | `min(2.0, 5.0)` | `2.0` | 2 |
| `RI[55]` | `sign(-3)` | `-1` | -1 |
| `RI[56]` | `sign(0)` | `0` | 0 |
| `RF[46]` | `pow(2.0, 10.0)` | `1024.0` | 1024=pow-fn-matches-** |
| `RF[47]` | `getexp(12.0)` | `UNSUPPORTED (unsupported function 'getexp')` | 3=IEEE 4=frexp |
| `RF[48]` | `getman(12.0)` | `UNSUPPORTED (unsupported function 'getman')` | 1.5=IEEE 0.75=frexp |
| `RF[49]` | `getexp(0.75)` | `UNSUPPORTED (unsupported function 'getexp')` | -1=IEEE 0=frexp |
| `RF[50]` | `getman(0.75)` | `UNSUPPORTED (unsupported function 'getman')` | 1.5=IEEE 0.75=frexp |
| `RF[51]` | `rand()` | `UNSUPPORTED (unsupported function 'rand')` | uniform [0,1) — report a sample |
| `RI[57]` | `sizeOfDim(A, 0)` | `UNSUPPORTED (unsupported function 'sizeOfDim')` | 5=length-of-A |
| `RF[52]` | `A[0]` | `10.0` | 10 |
| `RF[53]` | `A[n - 1]` | `50.0` | 50 |
| `RI[58]` | `sizeOfDim(B, 0)` | `UNSUPPORTED (unsupported function 'sizeOfDim')` | 2=rows |
| `RI[59]` | `sizeOfDim(B, 1)` | `UNSUPPORTED (unsupported function 'sizeOfDim')` | 3=cols |
| `RF[54]` | `B[1][2]` | `UNSUPPORTED (expected ';' but got '[' (line 1, col 9))` | 6=2D-indexing |
| `RF[55]` | `pi` | `3.141592653589793` | 3.14159265 |
| `RF[56]` | `1E3` | `1000.0` | 1000=accepts-exponent |
| `RF[57]` | `float32 s; s = 1.0 / 3.0; s` | `0.3333333333333333` | 0.3333333432=float32-truncates 0.3333333333=stays-double |
| `RX[0]` | `1 / 0` | `RUNTIME ZeroDivisionError: division by zero` | inf=int/int-is-real traps=int-div-by-zero |
| `RX[1]` | `1.0 / 0.0` | `RUNTIME ZeroDivisionError: float division by zero` | inf=IEEE traps=node-errors |
| `RX[2]` | `-1.0 / 0.0` | `RUNTIME ZeroDivisionError: float division by zero` | -inf=IEEE traps |
| `RX[3]` | `0.0 / 0.0` | `RUNTIME ZeroDivisionError: float division by zero` | nan=IEEE traps |
| `RX[4]` | `sqrt(-1.0)` | `RUNTIME ValueError: math domain error` | nan traps |
| `RX[5]` | `ln(0.0)` | `RUNTIME ValueError: math domain error` | -inf traps |
| `RX[6]` | `ln(-1.0)` | `RUNTIME ValueError: math domain error` | nan traps |
| `RX[7]` | `log(0.0)` | `RUNTIME ValueError: math domain error` | -inf traps |
| `RX[8]` | `acos(2.0)` | `RUNTIME ValueError: math domain error` | nan=out-of-domain traps |
| `RX[9]` | `(-8) ** (1.0 / 3.0)` | `(1.0000000000000002+1.7320508075688772j)` | nan=C-pow -2=real-cube-root |
| `RX[10]` | `0 ** 0` | `1` | 1 nan |
| `RX[11]` | `tan(pi / 2)` | `1.633123935319537e+16` | huge-finite inf |
