# PowerROM: Reduced Order Power System Model
## Development Note

**Version**: 0.1 (Draft)  
**Author**: PLANiT Institute  
**Last Updated**: 2026-04-30

---

## 1. 프로젝트 개요

### 1.1 핵심 아이디어

전력 계통의 복잡한 시간대별 시뮬레이션(Hourly Dispatch Simulation)을 **파라미터 기반 함수(Parametric Reduced Order Model)**로 치환하는 온라인 정책 분석 도구.

사용자는 **발전원별 비중(Share) 슬라이더**만 조작하면, 시스템 전체 비용·배출량·ESS 필요량이 즉시 산출된다.

### 1.2 설계 철학

```
복잡한 물리 계산은 백엔드에서 미리 끝낸다.
사용자에게는 슬라이더와 차트만 보인다.
파라미터는 사용자 데이터로 교체 가능하다.
```

- **Form(수식 구조)**: 시스템이 카탈로그로 제공
- **Parameters(수식 값)**: 사용자가 데이터/문헌/직접입력으로 채움
- **Default**: 국가별 문헌값(IEA, IRENA, OECD/NEA 등)으로 즉시 사용 가능

### 1.3 참조 방법론

- Reduced Order Modeling (ROM) / Surrogate Modeling
- VRE Integration Cost Functions (IEA, OECD/NEA)
- Load Duration Curve (LDC) 기반 Merit Order 분석
- Parametric LCOE (CAPEX/CF annuity 구조)

---

## 2. 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (React/Vercel)               │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Share Sliders│  │ Carbon Price │  │ Country/Mode │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         └─────────────────┴─────────────────┘           │
│                           │                             │
│              ┌────────────▼────────────┐                │
│              │    Chart Output Layer   │                │
│              │  - System LCOE curve    │                │
│              │  - Emission intensity   │                │
│              │  - ESS requirement      │                │
│              │  - Cost breakdown       │                │
│              └────────────┬────────────┘                │
└───────────────────────────┼─────────────────────────────┘
                            │ API calls
┌───────────────────────────▼─────────────────────────────┐
│                   Backend (Python/FastAPI)               │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │              Calculation Engine                  │    │
│  │                                                  │    │
│  │  compute_system_lcoe(shares, country, params)    │    │
│  │  compute_emissions(shares, country, params)      │    │
│  │  compute_ess_requirement(vre_share, params)      │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         │                               │
│  ┌──────────────────────▼──────────────────────────┐    │
│  │            Function Catalog Engine               │    │
│  │                                                  │    │
│  │  evaluate_function(func_type, params, x)         │    │
│  │  fit_curve(data_points, func_type)               │    │
│  │  validate_completeness(generator_config)         │    │
│  └──────────────────────┬──────────────────────────┘    │
│                         │                               │
│  ┌──────────────────────▼──────────────────────────┐    │
│  │              Parameter Store                     │    │
│  │                                                  │    │
│  │  country_profiles/  (KR, AU, JP, DE ...)        │    │
│  │  generator_defaults/ (solar, wind, gas ...)     │    │
│  │  user_configs/      (업로드된 사용자 설정)        │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 수식 구조 (Mathematical Framework)

### 3.1 시스템 LCOE 마스터 공식

```
System_LCOE(S, CP) = Σᵢ sᵢ × LCOE_i(CF_eff_i(S), CP)
                   + LCOE_ESS(VRE_share(S))

여기서:
  S  = {s_solar, s_wind, s_gas, s_coal, s_nuclear, ...}  (share vector, Σ=1)
  CP = Carbon Price [$/tCO2]
  i  = 각 발전원 인덱스
```

### 3.2 개별 발전원 LCOE

```
LCOE_i(CF_eff, CP) =
    CAPEX_i × CRF(r, n_i) / (CF_eff_i × 8760)   ← CAPEX 연간화
  + OPEX_fixed_i / (CF_eff_i × 8760)              ← 고정 O&M
  + OPEX_var_i                                     ← 변동 O&M
  + Fuel_i / η_i(CF_eff_i)                        ← 연료비 (효율 보정)
  + CP × EF_i / η_i(CF_eff_i)                    ← 탄소 비용
  + Integration_cost_i(sᵢ)                        ← 계통 통합 비용

CRF(r, n) = r(1+r)ⁿ / ((1+r)ⁿ - 1)
```

### 3.3 발전원별 핵심 함수 (모두 share/CF의 함수)

#### CF_eff(share): 실질 이용률 — 출력제한 반영

| 발전원 | 주요 감소 원인 | 권장 함수형태 |
|--------|-------------|-------------|
| Solar | VRE 총 share 증가 → curtailment | logarithmic decay |
| Wind | VRE 총 share 증가 → curtailment | logarithmic decay |
| Gas CCGT | VRE 증가 → 백업 역할 → 가동률 하락 | linear decay |
| Coal | Merit order 밀려남 | piecewise linear |
| Nuclear | 정책 결정 | constant (하한선) |

#### η(CF): 열효율 — 부분부하 특성

```
η_i(CF) = η_max_i - α_i × exp(-β_i × CF)
```
CF 낮을수록 효율 저하 → 연료비·탄소비용 상승 (석탄·가스에 중요)

#### Integration_cost(share): 계통 통합 비용

```
C_int(s) = a + b×s + c×s²    (quadratic, VRE 비중 증가에 비선형 상승)
```

#### ESS_requirement(VRE_share): ESS 필요 용량

```
ESS_cap(s_vre) = A × s_vre^B    (power function, 비선형 증가)
ESS_cost = ESS_cap × CAPEX_ESS × CRF / (cycles × DoD)

EV_offset = EV_penetration × avg_battery_kWh × V2G_factor
          → ESS 필요량에서 차감 가능
```

---

## 4. 함수 카탈로그

사용자가 각 발전원의 각 컴포넌트에 대해 선택 가능한 함수 형태:

```python
FUNCTION_CATALOG = {
    "linear":      lambda x, a, b: a + b * x,
    "logarithmic": lambda x, a, b, c: a - b * np.log(1 + c * x),
    "quadratic":   lambda x, a, b, c: a + b * x + c * x**2,
    "exponential": lambda x, a, b: a * np.exp(b * x),
    "power":       lambda x, a, b: a * x**b,
    "piecewise":   PiecewiseLinear,   # threshold + slope
    "constant":    lambda x, a: a,
    "logistic":    lambda x, L, k, x0: L / (1 + np.exp(-k * (x - x0))),
}
```

각 함수는: **입력 범위 제한(min/max)**, **신뢰구간**, **R² 표시** 포함

---

## 5. 파라미터 입력 방식 (3 Levels)

### Level A: Default (즉시 사용)
국가별 문헌값 자동 로드. 사용자 조작 없이 바로 차트 출력.

```
데이터 출처: IEA WEO 2024, IRENA 2024, OECD/NEA Integration Cost Study
```

### Level B: Excel 업로드

**Sheet 1: Generator_Config**
```
Generator | Component        | Func_Type   | Param_1 | Param_2 | Min  | Max
solar     | CF_eff           | logarithmic | 0.145   | 0.15    | 0.05 | 0.20
solar     | integration_cost | quadratic   | 4.1     | 0.8     | 0    | 50
gas_ccgt  | CF_backup        | linear      | 0.75    | -0.6    | 0.10 | 0.85
gas_ccgt  | eta              | logarithmic | 0.58    | 0.05    | 0.35 | 0.58
coal      | CF_merit         | piecewise   | 0.4     | 0.8     | 0.15 | 0.80
```

**Sheet 2: Historical_Data** (선택)
```
Year | VRE_share | Solar_CF | Wind_CF | Gas_CF | Coal_CF | System_LCOE | Emission_Intensity
2019 | 0.05      | 0.142    | 0.220   | 0.71   | 0.68    | 98.2        | 0.45
2020 | 0.07      | 0.139    | 0.215   | 0.68   | 0.65    | 97.8        | 0.43
```

**Sheet 3: Constraints**
```
Generator | Component | Min  | Max
solar     | share     | 0    | 0.70
nuclear   | CF_eff    | 0.80 | 0.95
```

### Level C: 포인트 직접 입력 → 자동 피팅
UI에서 (x, y) 포인트 최소 3개 입력 → 함수형태 선택 → scipy curve_fit 실행 → R²/신뢰구간 표시

---

## 6. 파라미터 완성도 체커

```
✅ 자동 추정 완료 (R² > 0.85):  CF_eff_solar, CF_eff_wind
⚠️  추정 완료, 신뢰도 낮음 (R² < 0.85, n < 5):  CF_backup_gas
❌  데이터 부족, 추정 불가:  Integration_cost_solar
📚 문헌 Default 사용 중:  CAPEX_all, OPEX_all, EF_all
```

→ 부족한 파라미터는 UI에서 명시적으로 표시 및 Default 권장

---

## 7. 국가 프로필 구조

```json
{
  "KR": {
    "name": "South Korea",
    "base_load_GW": 55,
    "peak_load_GW": 92,
    "annual_generation_TWh": 595,
    "grid_strength": "medium",
    "discount_rate": 0.05,
    "generators": {
      "solar": { "cf_base": 0.145, "capex_usd_kw": 900, ... },
      "wind_onshore": { "cf_base": 0.22, "capex_usd_kw": 1400, ... },
      "gas_ccgt": { "cf_base": 0.75, "fuel_usd_mmbtu": 12.0, ... },
      "coal": { "cf_base": 0.70, "fuel_usd_mmbtu": 3.5, ... },
      "nuclear": { "cf_base": 0.85, "capex_usd_kw": 4500, ... }
    },
    "integration_cost_coefficients": { "a": 4.1, "b": 0.8, "c": 1.2 },
    "ess_requirement_coefficients": { "A": 0.15, "B": 1.8 }
  }
}
```

초기 지원 국가: **KR, AU, JP, DE, GB** (확장 가능)

---

## 8. 출력 차트 목록

| 차트 | X축 | Y축 | 비고 |
|------|-----|-----|------|
| System LCOE Curve | VRE Share (%) | LCOE ($/MWh) | 발전원별 스택 |
| Cost Breakdown | VRE Share (%) | Cost component ($/MWh) | CAPEX/Fuel/Carbon/Integration |
| Emission Intensity | VRE Share (%) | gCO2/kWh | 신뢰구간 표시 |
| ESS Requirement | VRE Share (%) | GW / GWh | EV offset 분리 표시 |
| Carbon Price Sensitivity | Carbon Price ($/t) | System LCOE ($/MWh) | VRE share 고정 시 |
| Trade-off Frontier | LCOE ($/MWh) | Emission (gCO2/kWh) | Pareto curve |

---

## 9. 기술 스택

### Backend (Python)
```
FastAPI          ← REST API 서버
numpy / scipy    ← 수치 계산, curve fitting
pandas           ← 데이터 처리
openpyxl         ← Excel 파싱
pydantic         ← 데이터 검증
pytest           ← 테스트
```

### Frontend (React/TypeScript)
```
Next.js (App Router)   ← Vercel 배포 최적화
Recharts / Plotly.js   ← 인터랙티브 차트
shadcn/ui              ← UI 컴포넌트
xlsx (SheetJS)         ← 클라이언트 Excel 파싱
Tailwind CSS           ← 스타일링
```

### 배포
```
Frontend: Vercel (자동 배포)
Backend:  Vercel Functions (Python) 또는 Railway/Render
```

---

## 10. 디렉토리 구조

```
powerrom/
├── backend/
│   ├── main.py                    ← FastAPI 엔트리포인트
│   ├── api/
│   │   ├── calculate.py           ← /api/calculate 엔드포인트
│   │   ├── fit.py                 ← /api/fit (curve fitting)
│   │   └── validate.py            ← /api/validate (completeness check)
│   ├── core/
│   │   ├── lcoe_engine.py         ← LCOE 계산 핵심 로직
│   │   ├── function_catalog.py    ← 함수 카탈로그 (linear, log, ...)
│   │   ├── curve_fitter.py        ← scipy 기반 파라미터 피팅
│   │   └── completeness_checker.py
│   ├── data/
│   │   ├── country_profiles/
│   │   │   ├── KR.json
│   │   │   ├── AU.json
│   │   │   └── ...
│   │   └── generator_defaults/
│   │       ├── solar.json
│   │       ├── wind.json
│   │       ├── gas_ccgt.json
│   │       ├── coal.json
│   │       └── nuclear.json
│   ├── models/
│   │   └── schemas.py             ← Pydantic 모델
│   └── tests/
│       ├── test_lcoe_engine.py
│       └── test_curve_fitter.py
│
├── frontend/
│   ├── app/
│   │   ├── page.tsx               ← 메인 페이지
│   │   └── layout.tsx
│   ├── components/
│   │   ├── ShareSliders.tsx       ← 발전원 비중 슬라이더
│   │   ├── CarbonPriceSlider.tsx
│   │   ├── CountrySelector.tsx
│   │   ├── charts/
│   │   │   ├── SystemLcoeChart.tsx
│   │   │   ├── EmissionChart.tsx
│   │   │   ├── EssRequirementChart.tsx
│   │   │   └── TradeoffFrontier.tsx
│   │   ├── parameter/
│   │   │   ├── ExcelUploader.tsx
│   │   │   ├── PointInputForm.tsx
│   │   │   ├── FunctionSelector.tsx
│   │   │   └── CompletenessReport.tsx
│   │   └── ui/                    ← shadcn components
│   ├── lib/
│   │   ├── api.ts                 ← backend API 호출
│   │   └── excel-parser.ts        ← Excel 템플릿 파싱
│   └── public/
│       └── templates/
│           └── powerrom_template.xlsx
│
└── docs/
    ├── development_note.md        ← 이 문서
    └── parameter_guide.md         ← 파라미터 입력 가이드
```

---

## 11. API 엔드포인트 설계

### POST /api/calculate
```json
Request:
{
  "country": "KR",
  "shares": { "solar": 0.30, "wind": 0.10, "gas": 0.35, "coal": 0.15, "nuclear": 0.10 },
  "carbon_price": 30,
  "ev_penetration": 0.05,
  "custom_params": { ... }  // 선택: 사용자 정의 파라미터
}

Response:
{
  "system_lcoe": 112.4,
  "lcoe_by_generator": { "solar": 45.2, "gas": 89.3, ... },
  "emission_intensity": 0.28,
  "ess_requirement_gw": 12.4,
  "ess_requirement_gwh": 49.6,
  "curve_data": [
    { "vre_share": 0.0, "lcoe": 95.2, "emission": 0.55 },
    { "vre_share": 0.1, "lcoe": 97.1, "emission": 0.49 },
    ...
  ]
}
```

### POST /api/fit
```json
Request:
{
  "data_points": [[0.05, 0.142], [0.20, 0.130], [0.40, 0.112]],
  "func_type": "logarithmic",
  "bounds": { "min": [0, 0, 0], "max": [1, 1, 10] }
}

Response:
{
  "params": { "a": 0.145, "b": 0.15, "c": 2.1 },
  "r_squared": 0.94,
  "confidence_intervals": { "a": [0.140, 0.150], "b": [0.12, 0.18] },
  "sufficient_data": true
}
```

### POST /api/validate
```json
Request: { "generator_config": { ... } }
Response:
{
  "status": "partial",
  "components": {
    "CF_eff_solar":         { "status": "fitted",  "r2": 0.94 },
    "integration_cost_gas": { "status": "missing", "recommendation": "use_default" },
    "CAPEX_solar":          { "status": "default", "source": "IEA 2024" }
  }
}
```

---

## 12. 개발 단계 (Phases)

### Phase 1 (MVP): 한국 단일 국가, Default 파라미터
- [ ] Backend: LCOE 계산 엔진 (KR default)
- [ ] Backend: 5개 발전원 (solar, wind, gas, coal, nuclear)
- [ ] Frontend: Share 슬라이더 + Carbon price 슬라이더
- [ ] Frontend: System LCOE curve + Emission intensity 차트
- [ ] Vercel 배포

### Phase 2: 파라미터 커스터마이징
- [ ] Excel 업로드 + 파싱
- [ ] Curve fitting API + 신뢰구간
- [ ] 완성도 체커 UI
- [ ] ESS/EV 모듈

### Phase 3: 다국가 + 고급 기능
- [ ] AU, JP, DE, GB 국가 프로필 추가
- [ ] Trade-off Frontier (Pareto) 차트
- [ ] 시나리오 저장/비교
- [ ] 파라미터 가이드 문서화

---

## 13. 핵심 설계 결정사항 (Design Decisions)

1. **연간 합계 기반**: 시간대별 시뮬레이션 대신 연간 통계 파라미터 사용 → 웹 실시간 계산 가능
2. **함수 형태 분리**: 수식 구조(Form)와 값(Parameter)을 완전히 분리 → 사용자 커스터마이징 가능
3. **신뢰구간 명시**: 데이터 부족 시 불확실성을 숨기지 않고 시각화
4. **Default-first**: 즉시 사용 가능한 문헌값 제공, 사용자 입력은 선택사항
5. **Share constraint**: 슬라이더 합계 = 100% 강제, 변경 시 비례 조정

---

*본 문서는 개발 진행에 따라 지속 업데이트됩니다.*
