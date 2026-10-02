/** @type {import("jest").Config} **/
module.exports = {
    testEnvironment: 'jsdom',
    // Lowlight exposes ESM; transform it and its ESM assertion helper for Jest.
    transformIgnorePatterns: ['/node_modules/(?!(lowlight|devlop)/)'],
    transform: {
        '^.+/node_modules/(lowlight|devlop)/.+\\.js$': ['babel-jest', {
            babelrc: false,
            configFile: false,
            presets: [['@babel/preset-env', { targets: { node: 'current' } }]],
        }],
        '^.+\\.tsx?$': 'ts-jest',
        '^.+\\.jsx?$': 'babel-jest',
    },
    collectCoverageFrom: ['src/**'],
};
