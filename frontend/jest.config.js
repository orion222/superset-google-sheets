module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'jsdom',
  moduleNameMapper: {
    '^@apache-superset/core$': '<rootDir>/test/coreMock.ts',
  },
};
