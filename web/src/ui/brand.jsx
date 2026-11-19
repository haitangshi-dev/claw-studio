/* 品牌标记：像素风螃蟹。
   16×16 手工点阵，只有两色（主色 + 前景色），shapeRendering=crispEdges 保证硬边。 */

const MAP = [
  '................',
  '................',
  '................',
  '................',
  '......o..o......',
  '......#..#......',
  '.##..######..##.',
  '.#.##########.#.',
  '.#.##########.#.',
  '.##.########.##.',
  '.....######.....',
  '....#.#..#.#....',
  '...#.#....#.#...',
  '................',
  '................',
  '................',
]

const FILL = { '#': 'var(--primary)', o: 'var(--primary-fg)' }

function runs() {
  const out = []
  MAP.forEach((row, y) => {
    let x = 0
    while (x < row.length) {
      const ch = row[x]
      if (ch === '.') {
        x++
        continue
      }
      let w = 1
      while (x + w < row.length && row[x + w] === ch) w++
      out.push({ x, y, w, fill: FILL[ch] })
      x += w
    }
  })
  return out
}

const CELLS = runs()

export function CrabMark({ size = 32 }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      shapeRendering="crispEdges"
      aria-hidden="true"
    >
      {CELLS.map((c, i) => (
        <rect key={i} x={c.x} y={c.y} width={c.w} height="1" fill={c.fill} />
      ))}
    </svg>
  )
}

export function Wordmark({ size = 13, sub }) {
  return (
    <div className="min-w-0">
      <p className="m-0 font-medium leading-tight" style={{ fontSize: size }}>
        Claw Studio
      </p>
      {sub && <p className="m-0 text-[11px] leading-tight text-faint">{sub}</p>}
    </div>
  )
}
