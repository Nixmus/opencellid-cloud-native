CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS cells (
    radio      text             NOT NULL,
    mcc        integer          NOT NULL,
    net        integer          NOT NULL,
    area       integer          NOT NULL,
    cell       bigint           NOT NULL,
    unit       integer,
    lon        double precision NOT NULL,
    lat        double precision NOT NULL,
    range_m    integer          NOT NULL,
    samples    integer          NOT NULL,
    created_at timestamptz      NOT NULL,
    updated_at timestamptz      NOT NULL,
    geom       geography(Point, 4326) NOT NULL,
    PRIMARY KEY (radio, mcc, net, area, cell)
);

CREATE INDEX IF NOT EXISTS cells_geom_idx ON cells USING GIST (geom);
CREATE INDEX IF NOT EXISTS cells_net_idx ON cells (mcc, net);
