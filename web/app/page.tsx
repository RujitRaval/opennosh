export default function Home() {
  return (
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <p className="eyebrow">The open food log</p>
        <h1 id="hero-title">Know what fuels your next set.</h1>
        <p className="lede">
          opennosh is a self-hosted nutrition and strength tracker built around food data the
          community can improve.
        </p>
        <dl className="status" aria-label="Foundation status">
          <div>
            <dt>Nutrition</dt>
            <dd>Foundation ready</dd>
          </div>
          <div>
            <dt>Strength</dt>
            <dd>Foundation ready</dd>
          </div>
          <div>
            <dt>Food data</dt>
            <dd>Community owned</dd>
          </div>
        </dl>
      </section>
    </main>
  );
}
