/*
   Persistent row-level translation failures.

   The application/job creates dbo.TranslatorTranslationFailureLog
   automatically during its first execute run. This script is an optional
   manual provisioning alternative.
*/
IF OBJECT_ID(N'[dbo].[TranslatorTranslationFailureLog]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[TranslatorTranslationFailureLog]
    (
        [FailureId] BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT [PK_TranslatorTranslationFailureLog] PRIMARY KEY,
        [FailureFingerprint] VARCHAR(64) NOT NULL,
        [SchemaName] SYSNAME NOT NULL,
        [TableName] SYSNAME NOT NULL,
        [SourceLanguageId] INT NOT NULL,
        [TargetLanguageId] INT NOT NULL,
        [EntityKeyJson] NVARCHAR(MAX) NOT NULL,
        [SourceRowJson] NVARCHAR(MAX) NOT NULL,
        [TargetExists] BIT NOT NULL
            CONSTRAINT [DF_TranslatorTranslationFailureLog_TargetExists] DEFAULT (0),
        [MissingColumnsJson] NVARCHAR(MAX) NULL,
        [FailureReason] NVARCHAR(MAX) NOT NULL,
        [FirstFailedAt] DATETIME2(3) NOT NULL
            CONSTRAINT [DF_TranslatorTranslationFailureLog_FirstFailedAt] DEFAULT (SYSUTCDATETIME()),
        [LastFailedAt] DATETIME2(3) NOT NULL
            CONSTRAINT [DF_TranslatorTranslationFailureLog_LastFailedAt] DEFAULT (SYSUTCDATETIME()),
        [AttemptCount] INT NOT NULL
            CONSTRAINT [DF_TranslatorTranslationFailureLog_AttemptCount] DEFAULT (1),
        CONSTRAINT [CK_TranslatorTranslationFailureLog_AttemptCount]
            CHECK ([AttemptCount] >= 1)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'[dbo].[TranslatorTranslationFailureLog]')
      AND name = N'UX_TranslatorTranslationFailureLog_Fingerprint'
)
BEGIN
    CREATE UNIQUE INDEX [UX_TranslatorTranslationFailureLog_Fingerprint]
        ON [dbo].[TranslatorTranslationFailureLog] ([FailureFingerprint]);
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'[dbo].[TranslatorTranslationFailureLog]')
      AND name = N'IX_TranslatorTranslationFailureLog_Queue'
)
BEGIN
    CREATE INDEX [IX_TranslatorTranslationFailureLog_Queue]
        ON [dbo].[TranslatorTranslationFailureLog]
        ([SourceLanguageId], [TargetLanguageId], [LastFailedAt], [FailureId]);
END;
GO
